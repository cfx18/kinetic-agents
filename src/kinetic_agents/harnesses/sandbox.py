"""Reuse the qualified OS isolation primitive, never Codex's model/agent loop.

Claude/Kimi run inside an outer named filesystem sandbox. The only host API
access uses scoped loopback tokens. The upstream credential is never mounted.
"""

from pathlib import Path
import json
import os
import shutil
import subprocess

from kinetic_agents.native.permissions import flags
from kinetic_agents.core.storage import atomic

NAME = "external-native-research"


def command(work, task, home, binding, argv):
    binary = Path(binding["executable"]).resolve()
    # Python/Node packaged CLIs need their installed code, not the user's home.
    runtimes = [binary]
    if "/uv/tools/" in str(binary):
        runtimes.append(binary.parent.parent)
        for path in binary.parent.iterdir():
            if path.name.startswith("python") and path.is_symlink():
                runtimes.append(path.resolve().parent.parent)
    if "/node_modules/" in str(binary):
        runtimes.append(Path(str(binary).split("/node_modules/")[0]) / "node_modules")
    filesystem = {
        ":root": "deny",
        ":minimal": "read",
        # TMPDIR is this actor's private home/tmp. Denying :tmpdir would mask
        # our own scratch even though home is writable. Host /tmp stays denied.
        ":slash_tmp": "deny",
        str(Path(work).resolve()): "write",
        str(Path(task).resolve()): "read",
        str(Path(home).resolve()): "write",
    }
    for path in runtimes:
        filesystem[str(path)] = "read"
    settings = {
        "default_permissions": NAME,
        f"permissions.{NAME}.filesystem": filesystem,
        f"permissions.{NAME}.network.enabled": True,
    }
    codex = shutil.which("codex")
    if not codex:
        raise FileNotFoundError(
            "external harness isolation currently requires the local codex sandbox binary"
        )
    return [
        codex,
        *flags(settings),
        "sandbox",
        "-P",
        NAME,
        "-C",
        str(work),
        "--",
        *argv,
    ]


def environment(home):
    (home / "sandbox-config").mkdir(mode=0o700, parents=True, exist_ok=True)
    (home / "tmp").mkdir(mode=0o700, exist_ok=True)
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "CODEX_HOME": str(home / "sandbox-config"),
        "BASH_ENV": "",
        "ENV": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "DO_NOT_TRACK": "1",
        "NO_COLOR": "1",
    }


def qualify(root, task, binding):
    home = root / "native" / "isolation-probe"
    home.mkdir(parents=True, exist_ok=True)
    canary = root / "native" / "isolation-private.txt"
    if not canary.exists():
        with canary.open("x") as stream:
            stream.write("Synthetic host-only isolation canary; never scientific data.\n")
    # Synthetic inspection only; no account, model, scheduler or science.
    source = """import json,os,sys,tempfile
from pathlib import Path
task,forbidden=map(Path,sys.argv[1:])
assert (task/'TASK.md').read_text()
try: forbidden.read_bytes()
except OSError: pass
else: raise RuntimeError('host artifact readable')
try: (task/'forbidden-write').write_text('bad')
except OSError: pass
else: raise RuntimeError('task writable')
features={}
try:
    with open('/proc/self/maps','rb') as stream: stream.read(1)
    features['self_maps']=True
except OSError: features['self_maps']=False
try:
    fd=os.open(task,os.O_RDONLY|os.O_DIRECTORY)
    try: features['self_fd']=os.readlink('/proc/self/fd/'+str(fd))==str(task)
    finally: os.close(fd)
except OSError: features['self_fd']=False
with tempfile.TemporaryFile(dir=os.environ['TMPDIR']) as stream:
    stream.write(b'owned scratch test')
features['private_tmp_writable']=True
print('RUNTIME_FEATURES='+json.dumps(features))
print('ISOLATION_OK')
"""
    argv = ["/usr/bin/python3", "-I", "-c", source, str(task), str(canary)]
    result = subprocess.run(
        command(root / "work", task, home, binding, argv),
        env=environment(home),
        capture_output=True,
        timeout=30,
    )
    ok = result.returncode == 0 and b"ISOLATION_OK" in result.stdout
    features = {}
    for line in result.stdout.splitlines():
        if line.startswith(b"RUNTIME_FEATURES="):
            try:
                value = json.loads(line.split(b"=", 1)[1])
                if isinstance(value, dict):
                    features = {
                        key: value.get(key) is True
                        for key in ("self_maps", "self_fd", "private_tmp_writable")
                    }
            except ValueError:
                pass
    atomic(
        root / "native/isolation.json",
        {
            "status": "PASS" if ok else "FAIL",
            "returncode": result.returncode,
            "runtime_features": features,
            "scope": "filesystem probe; not full network isolation or paid model qualification",
        },
    )
    if not ok:
        raise PermissionError(
            "external harness filesystem isolation probe failed before starting budget clocks"
        )
    if binding["name"] == "claude_code" and not all(
        features.get(key) for key in ("self_maps", "self_fd")
    ):
        atomic(
            root / "native/bootstrap.json",
            {
                "status": "BLOCKED",
                "harness": "claude_code",
                "model_calls": 0,
                "error_code": "CLAUDE_PROCESS_METADATA_UNAVAILABLE",
                "runtime_features": features,
                "scope": "own-process runtime metadata only; no other processes inspected",
                "next_action": "Use an isolated worker with private procfs; do not expose host /proc",
            },
        )
        raise PermissionError(
            "Claude runtime requires /proc/self/maps and /proc/self/fd, unavailable in this sandbox; "
            "no budget clocks or model requests started. See native/bootstrap.json"
        )
    bootstrap = subprocess.run(
        command(root / "work", task, home, binding, [binding["executable"], "--help"]),
        env=environment(home),
        capture_output=True,
        timeout=30,
    )
    ok = bootstrap.returncode == 0 and bool(bootstrap.stdout)
    atomic(
        root / "native/bootstrap.json",
        {
            "status": "PASS" if ok else "FAIL",
            "returncode": bootstrap.returncode,
            "harness": binding["name"],
            "model_calls": 0,
            "scope": "CLI --help inside the same filesystem sandbox, no API credentials",
        },
    )
    if not ok:
        raise PermissionError(
            "native CLI cannot bootstrap inside isolation; no budget clocks or model requests started"
        )
