"""Generic, asynchronous batch execution for the approved open-world pair.

No chemistry methods, cases or model calls. Agent code executes ONLY in the
qualified container on a cfx N1/n64 allocation. The host transfers regular files,
reserves allocated core-time before submit, and never retries an ambiguous submit.
One background reconciler per arm; observations cannot submit new work.
"""

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tarfile
import threading
import time

from kinetic_agents.core.storage import atomic
from kinetic_agents.execution.slurm import checked
from kinetic_agents.execution.slurm import transfer
from kinetic_agents.execution.slurm import scheduler
from kinetic_agents.execution.slurm import cancel
from kinetic_agents.execution.slurm import remote_path
from kinetic_agents.execution.deployment import validate_deployment

TERMINAL = {
    "COMPLETED",
    "FAILED",
    "TIMEOUT",
    "CANCELLED",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "PREEMPTED",
}
MAX_FILE = 64 * 1024 * 1024
MAX_INPUT = 256 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
LOCAL_OWNER_CEILING = 48 * 3600


def read_regular(root, relative, limit=MAX_FILE):
    """No-follow descriptor walk; no FIFO blocking or symlink-swap escape."""
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or any(p in ("", ".", "..") for p in relative.split("/"))
    ):
        raise PermissionError("relative regular work file required")
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise PermissionError("linked work root")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise PermissionError("regular bounded single-link file required")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("input changed during snapshot")
            return data
    finally:
        os.close(directory)


def unpack_regular(blob, target, limit=MAX_OUTPUT):
    """Host archive only. Never tar.extractall on outputs from agent code."""
    target = Path(target)
    if target.exists():
        raise FileExistsError("new host archive directory required")
    if len(blob) > limit + 1024 * 1024:
        raise ValueError("output archive too large")
    members = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
        seen = set()
        for m in archive:
            if (
                not m.isfile()
                or m.name in seen
                or "\\" in m.name
                or any(p in ("", ".", "..") for p in m.name.split("/"))
            ):
                raise PermissionError("unsafe or duplicate output member")
            seen.add(m.name)
            total += m.size
            if m.size > MAX_FILE or total > limit or len(seen) > 4096:
                raise ValueError("output bounds exceeded")
            data = archive.extractfile(m).read(m.size + 1)
            if len(data) != m.size:
                raise ValueError("incomplete output")
            members.append((m.name, data))
    target.mkdir(mode=0o700)
    result = []
    for name, data in members:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as f:
            f.write(data)
        path.chmod(0o400)
        result.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    return result


def write_new_regular(root, relative, data):
    """Publish into the Agent workspace without following its directory links."""
    if (
        not isinstance(relative, str)
        or "\\" in relative
        or any(p in ("", ".", "..") for p in relative.split("/"))
    ):
        raise PermissionError("safe relative destination required")
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise PermissionError("linked work root")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(
            parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    finally:
        os.close(directory)


# This trusted helper runs on the allocated compute node, never on login. The
# child container cannot read this script, registry config, other jobs or SIF.
RUNNER = """import hashlib, io, json, os, pathlib, signal, subprocess, tarfile, time
root=pathlib.Path(__file__).parent
site_config=pathlib.Path('/public3/soft/singularity/singularity-3.9.9/etc/singularity/singularity.conf')
assert hashlib.sha256(site_config.read_bytes()).hexdigest()=='f26e7fac27a2e99eb2bbe8b0ac37b7a46c1c03b591ab1ab60a1bfb5f79ee02cf', 'qualified site mounts changed'
allowed=sorted(os.sched_getaffinity(0))[:64]
assert len(allowed)==64, '64 allocated CPUs required'
os.sched_setaffinity(0,allowed)
request=json.loads((root/'request.json').read_text())
image=pathlib.Path(request['image_path'])
assert hashlib.sha256(image.read_bytes()).hexdigest()==request['image_sha256']
work=root/'work';work.mkdir()
with tarfile.open(root/'input.tar','r:') as archive:
    for m in archive:
        assert m.isfile() and all(x not in ('','.','..') for x in m.name.split('/'))
        path=work/m.name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f: f.write(archive.extractfile(m).read())
env=dict(os.environ)
for key in list(env):
    if key.startswith('SINGULARITYENV_') or key in ('SINGULARITY_BIND','SINGULARITY_BINDPATH','PYTHONPATH','LD_PRELOAD'):
        env.pop(key)
command=['singularity','exec','--containall','--cleanenv','--no-home','--no-mount','hostfs,bind-paths,cwd',
    '--net','--network','none','--no-privs','--drop-caps','ALL','--bind',str(work)+':/work:rw',
    '--pwd','/work',str(image),*request['argv']]
child=None
def stop(*_):
    if child is not None: child.terminate()
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGUSR1,stop)
with (root/'program.log').open('wb') as log:
    assert time.time()<request['deadline']-75, 'original account window closed'
    child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
    try: code=child.wait(timeout=max(1,min(request['minutes']*60-75,request['deadline']-time.time()-60)))
    except subprocess.TimeoutExpired:
        child.terminate()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill();child.wait()
        code=124
files=[];total=0
with tarfile.open(root/'output.tar','w:') as archive:
    for name in request['outputs']:
        path=work/name
        if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)) or path.stat().st_nlink!=1: continue
        if path.stat().st_size>67108864 or total+path.stat().st_size>67108864: continue
        total+=path.stat().st_size;archive.add(path,arcname=name,recursive=False);files.append(name)
    with (root/'program.log').open('rb') as log:
        log.seek(max(0,log.seek(0,2)-262144));data=log.read(262144)
    item=tarfile.TarInfo('__program_log__.txt');item.size=len(data);archive.addfile(item,io.BytesIO(data))
(root/'receipt.json').write_text(json.dumps(dict(exit_code=code,files=files,output_bytes=total,
    request_sha256=hashlib.sha256((root/'request.json').read_bytes()).hexdigest())))
"""


def batch_script(remote, name, minutes):
    remote_path(remote)
    if (
        not re.fullmatch(r"cfx_[A-Za-z0-9_-]+", name)
        or type(minutes) != int
        or not 2 <= minutes <= 120
    ):
        raise ValueError("bounded cfx N1/n64 batch required")
    return "\n".join(
        [
            "#!/bin/bash",
            f"#SBATCH --job-name={name}",
            "#SBATCH -N 1",
            "#SBATCH -n 64",
            "#SBATCH --partition=amd_256",
            f"#SBATCH --time={minutes//60:02}:{minutes%60:02}:00",
            "#SBATCH --signal=B:USR1@45",
            f"#SBATCH --chdir={remote}",
            f"#SBATCH --output={remote}/batch.out",
            f"#SBATCH --error={remote}/batch.err",
            "set -eo pipefail",
            "source /public3/home/sca2070/WORK/Caifeixue/.bashrc",
            "module load singularity/3.9.9",
            "unset PYTHONPATH LD_PRELOAD",
            f"python3 {remote}/runner.py &",
            "runner_pid=$!",
            "trap 'kill -USR1 \"$runner_pid\" 2>/dev/null || true' USR1 TERM",
            'wait "$runner_pid"',
            "",
        ]
    )


class RemoteJobs:
    def __init__(self, root, store, *, transport=True):
        self.root = Path(root).absolute()
        self.store = store
        self.transport = transport
        self.arm = store.get("contract")["arm"]
        if self.arm not in ("solo-max", "team-max"):
            raise PermissionError("approved arm required")
        self.archive = self.root / "remote_jobs"
        self.archive.mkdir(mode=0o700, exist_ok=True)
        self.stop_event = threading.Event()
        self.thread = None
        self.before_dispatch = None
        self.after_terminal = None
        with store.tx() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS remote_jobs(id TEXT PRIMARY KEY, actor TEXT, digest TEXT, value TEXT)"
            )

    def rows(self):
        import sqlite3

        with sqlite3.connect(self.store.path.as_uri() + "?mode=ro", uri=True) as db:
            return [
                json.loads(v) for v, in db.execute("SELECT value FROM remote_jobs ORDER BY rowid")
            ]

    def _put(self, row):
        with self.store.tx() as db:
            db.execute("UPDATE remote_jobs SET value=? WHERE id=?", (json.dumps(row), row["id"]))
            self.store._event(db, "REMOTE_JOB_STATE", dict(id=row["id"], status=row["status"]))

    def budget(self):
        rows = self.rows()
        setup = self.store.get(
            "qualification_cpu_charged_seconds",
            self.store.get("qualification_cpu_reserved_seconds", 0),
        )
        remote = sum(r.get("charged_seconds", r["reserved_seconds"]) for r in rows)
        cap = self.store.get("contract")["cpu_seconds"]
        prior = self.store.get("prior_attempt_cpu_seconds", 0)
        return dict(
            search_cpu_limit_seconds=cap,
            qualification_cpu_seconds=setup,
            prior_attempt_cpu_seconds=prior,
            remote_charged_or_reserved_seconds=remote,
            local_execution_reserved_seconds=LOCAL_OWNER_CEILING,
            remote_admission_remaining_seconds=max(
                0, cap - setup - prior - remote - LOCAL_OWNER_CEILING
            ),
        )

    def submit(self, actor, args, request_id):
        if set(args) - {"argv", "inputs", "outputs", "minutes", "_skill"} or not {
            "argv",
            "inputs",
            "outputs",
            "minutes",
        } <= set(args):
            raise ValueError("argv/inputs/outputs/minutes required")
        argv = args["argv"]
        minutes = args["minutes"]
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 128
            or any(not isinstance(v, str) or len(v) > 8000 or "\0" in v for v in argv)
            or type(minutes) != int
            or not 2 <= minutes <= 120
        ):
            raise ValueError("bounded argv and 2..120 minutes required")
        for key in ("inputs", "outputs"):
            if (
                not isinstance(args[key], list)
                or len(args[key]) > 4096
                or len(set(args[key])) != len(args[key])
            ):
                raise ValueError("unique explicit relative input/output files required")
            for name in args[key]:
                if (
                    not isinstance(name, str)
                    or "\\" in name
                    or len(name) > 512
                    or any(v in ("", ".", "..") for v in name.split("/"))
                    or name == "__program_log__.txt"
                ):
                    raise PermissionError("relative explicit work files only")
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        ident = "job_" + hashlib.sha256((actor + request_id).encode()).hexdigest()[:24]
        reserve = (minutes * 60 + 60) * 64  # Include Slurm termination/accounting grace.
        setup = self.store.get(
            "qualification_cpu_charged_seconds",
            self.store.get("qualification_cpu_reserved_seconds", 0),
        )
        with self.store.tx() as db:
            prior = db.execute(
                "SELECT actor,digest,value FROM remote_jobs WHERE id=?", (ident,)
            ).fetchone()
            if prior:
                if prior[:2] != (actor, digest):
                    raise PermissionError("request identity conflict")
                return json.loads(prior[2])
            now = time.time()
            deadline = self.store._get(db, "deadline")
            if (
                self.store._get(db, "status")
                in {"COMPLETED", "CANCELLED", "FAILED_REVIEW", "BUDGET_STOPPED", "FINALIZING"}
                or now + minutes * 60 >= deadline
            ):
                raise PermissionError("original run ended or insufficient wall time")
            rows = [json.loads(v) for v, in db.execute("SELECT value FROM remote_jobs")]
            used = sum(r.get("charged_seconds", r["reserved_seconds"]) for r in rows)
            prior = self.store._get(db, "prior_attempt_cpu_seconds", 0)
            if (
                used + reserve + setup + prior + LOCAL_OWNER_CEILING
                > self.store._get(db, "contract")["cpu_seconds"]
            ):
                raise PermissionError("allocated-core budget cannot admit this job")
            row = dict(
                id=ident,
                actor=actor,
                args=args,
                status="QUEUED",
                reserved_seconds=reserve,
                created=now,
                job_name="cfx_" + self.arm.replace("-", "_") + "_" + ident[4:16],
            )
            db.execute(
                "INSERT INTO remote_jobs VALUES(?,?,?,?)", (ident, actor, digest, json.dumps(row))
            )
            self.store._event(
                db, "REMOTE_JOB_QUEUED", dict(id=ident, actor=actor, reserved_seconds=reserve)
            )
        return {k: row[k] for k in ("id", "status", "reserved_seconds")}

    def start(self):
        if not self.transport:
            raise PermissionError("test backend cannot submit")
        self.thread = threading.Thread(
            target=self._loop, name="owned-slurm-reconciler", daemon=True
        )
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.step()
            except Exception as exc:
                atomic(
                    self.archive / "transport_health.json",
                    dict(error_type=type(exc).__name__, at=time.time()),
                )
            self.stop_event.wait(10)

    def step(self):
        rows = self.rows()
        for row in rows:
            # Only this reconciler calls _dispatch synchronously. A SUBMITTING
            # row at the start of a later step was left by an interrupted
            # dispatch; never submit it again without accounting reconciliation.
            if row["status"] == "SUBMITTING":
                row.update(status="SUBMIT_UNCERTAIN", error_type="InterruptedSubmission")
                self._put(row)
            if row["status"] in ("SUBMITTED", "RUNNING", "ACCOUNTING_PENDING"):
                state = scheduler(row)
                row["allocation"] = state
                if state["state"] in TERMINAL:
                    row["charged_seconds"] = state["allocated_core_seconds"]
                    row["status"] = "COLLECTING"
                    self._put(row)
                else:
                    row["status"] = (
                        state["state"]
                        if state["state"] in ("RUNNING", "ACCOUNTING_PENDING")
                        else "SUBMITTED"
                    )
                    self._put(row)
            if row["status"] == "COLLECTING":
                self._collect(row)
            if row["status"] in ("SETTLED", "REJECTED") and self.after_terminal:
                self.after_terminal(row)
        if any(
            r["status"] not in ("SETTLED", "REJECTED")
            for r in self.rows()
            if r["status"] != "QUEUED"
        ):
            return
        queued = [r for r in self.rows() if r["status"] == "QUEUED"]
        if queued:
            self._dispatch(queued[0])

    def _dispatch(self, row):
        account = self.store.read_state(("deadline", "status"))
        if (
            account["status"]
            in {"COMPLETED", "CANCELLED", "FAILED_REVIEW", "BUDGET_STOPPED", "FINALIZING"}
            or time.time() + row["reserved_seconds"] / 64 >= account["deadline"]
        ):
            row.update(status="REJECTED", charged_seconds=0, error_type="RunEnded")
            self._put(row)
            return
        directory = self.archive / row["id"]
        directory.mkdir(mode=0o700, exist_ok=True)
        deployment = validate_deployment(self.store.get("contract")["deployment"])
        remote = (
            deployment["search_root"] + "/" + self.store.get("contract")["run_id"] + "/" + row["id"]
        )
        row["remote"] = remote
        # Failed preparation has not submitted anything; no retry costs hidden.
        try:
            total = 0
            manifest = {}
            with tarfile.open(directory / "input.tar", "w:") as out:
                for name in row["args"]["inputs"]:
                    data = read_regular(self.root / "work", name)
                    total += len(data)
                    if total > MAX_INPUT:
                        raise ValueError("explicit inputs exceed 256MiB")
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    out.addfile(info, io.BytesIO(data))
                    manifest[name] = hashlib.sha256(data).hexdigest()
            q = json.loads((self.root.parent / "qualification/accepted.json").read_text())
            if q["status"] != "PASS":
                raise PermissionError("container not qualified")
            sha = q["image_sha256"]
            if not re.fullmatch("[0-9a-f]{64}", sha):
                raise ValueError("image digest missing")
            atomic(
                directory / "request.json",
                {
                    **row["args"],
                    "image_sha256": sha,
                    "image_path": deployment["container_image"],
                    "deadline": self.store.get("deadline"),
                },
            )
            (directory / "runner.py").write_text(RUNNER)
            (directory / "job.sh").write_text(
                batch_script(remote, row["job_name"], row["args"]["minutes"])
            )
            row["input_manifest"] = manifest
            if row["args"].get("_skill"):
                if not self.before_dispatch:
                    raise PermissionError("skill receipt service is not connected")
                self.before_dispatch(row, manifest)
            checked("mkdir -p " + shlex.quote(remote))
            for name in ("input.tar", "request.json", "runner.py", "job.sh"):
                transfer(directory / name, remote + "/" + name)
        except Exception as exc:
            row.update(status="REJECTED", charged_seconds=0, error_type=type(exc).__name__)
            self._put(row)
            return
        # A live RPC is not a failed/ambiguous RPC. Observers can see this
        # durable intent while sbatch is in flight without killing the run.
        row["status"] = "SUBMITTING"
        self._put(row)
        try:
            output = checked("sbatch --parsable " + remote + "/job.sh")
            match = re.fullmatch(r"([0-9]+)(?:;[^\n]+)?\s*", output)
            if not match:
                raise ConnectionError("ambiguous submission; never automatically duplicate")
        except Exception as exc:
            row.update(status="SUBMIT_UNCERTAIN", error_type=type(exc).__name__)
            self._put(row)
            raise
        row.update(status="SUBMITTED", job_id=match[1])
        self._put(row)

    def _collect(self, row):
        directory = self.archive / row["id"]
        target = directory / "output.tar"
        remote = row["remote"] + "/output.tar"
        check = checked(
            "if test -f "
            + remote
            + " && test ! -L "
            + remote
            + "; then stat -c %s "
            + remote
            + "; else echo MISSING; fi"
        ).strip()
        if check == "MISSING":
            row.update(status="SETTLED", output_status="UNAVAILABLE", outputs=[])
            self._put(row)
            return
        if not check.isdigit() or int(check) > MAX_OUTPUT + 1024 * 1024:
            raise PermissionError("remote output bounds")
        result = subprocess.run(
            ["scp", "-q", "sca2070:" + remote, str(target)], capture_output=True, timeout=60
        )
        if result.returncode:
            raise ConnectionError("output transfer pending")
        dest = directory / "output"
        manifest_path = directory / "output_manifest.json"
        manifest = (
            unpack_regular(target.read_bytes(), dest)
            if not dest.exists()
            else (
                json.loads(manifest_path.read_text())
                if manifest_path.exists()
                else row.get("outputs")
            )
        )
        if manifest is None:
            raise PermissionError("partial local archive; reconcile without rerunning science")
        atomic(manifest_path, manifest)
        receipt_path = row["remote"] + "/receipt.json"
        result = checked(
            "if test -f "
            + receipt_path
            + " && test ! -L "
            + receipt_path
            + "; then head -c 65536 "
            + receipt_path
            + "; else echo MISSING; fi"
        ).strip()
        if result != "MISSING":
            receipt = json.loads(result)
            if (
                receipt.get("request_sha256")
                != hashlib.sha256((directory / "request.json").read_bytes()).hexdigest()
            ):
                raise PermissionError("foreign execution receipt")
            row["execution_receipt"] = receipt
        row.update(status="SETTLED", output_status="ARCHIVED", outputs=manifest)
        self._put(row)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=65)
        if self.thread and self.thread.is_alive():
            raise RuntimeError("remote reconciler still in flight; retain pending state")
        for row in self.rows():
            if row["status"] == "QUEUED":
                row.update(status="REJECTED", charged_seconds=0, error_type="RunEnded")
                self._put(row)
            elif row.get("job_id") and row["status"] in (
                "SUBMITTED",
                "RUNNING",
                "ACCOUNTING_PENDING",
            ):
                cancel(row)
        # Collection remains possible after a deadline. No new model or compute.
