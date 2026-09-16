"""Extracted reusable implementation; historical launchers intentionally excluded."""

import json, hashlib, re, shlex, subprocess, time
from pathlib import Path
from pathlib import PurePosixPath

BASE = PurePosixPath("/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs")


def remote_path(value):
    p = PurePosixPath(value)
    if (
        not p.is_relative_to(BASE)
        or p == BASE
        or ".." in p.parts
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", str(p))
    ):
        raise PermissionError("owned Caifeixue remote path required")
    return p


def ssh(command, *, timeout=30):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "sca2070", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def checked(command, *, timeout=30):
    result = ssh(command, timeout=timeout)
    if result.returncode:
        raise ConnectionError("login file/scheduler transport unavailable")
    return result.stdout


def transfer(local, remote):
    remote = remote_path(remote)
    result = subprocess.run(
        ["scp", "-q", str(local), "sca2070:" + str(remote)], capture_output=True, timeout=60
    )
    if result.returncode:
        raise ConnectionError("owned file transfer unavailable")


def script(spool, release, python, name, minutes):
    spool, release, python = map(remote_path, (spool, release, python))
    if (
        not re.fullmatch(r"cfx_[A-Za-z0-9_-]+", name)
        or type(minutes) is not int
        or not 1 <= minutes <= 80
    ):
        raise PermissionError("cfx job name and bounded allocation required")
    return "\n".join(
        [
            "#!/bin/bash",
            f"#SBATCH --job-name={name}",
            "#SBATCH -N 1",
            "#SBATCH -n 64",
            "#SBATCH --partition=amd_256",
            f"#SBATCH --time={minutes//60:02}:{minutes%60:02}:00",
            f"#SBATCH --chdir={release}",
            f"#SBATCH --output={spool}/slurm-%j.out",
            f"#SBATCH --error={spool}/slurm-%j.err",
            "set -euo pipefail",
            "export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1",
            "unset PYTHONPATH LD_PRELOAD LD_LIBRARY_PATH",
            shlex.join(
                [
                    str(python),
                    "-B",
                    "-m",
                    "kinetic_agents.execution.slurm_worker",
                    "--spool",
                    str(spool),
                ]
            ),
            "",
        ]
    )


def scheduler(binding):
    job = binding["job_id"]
    if not re.fullmatch("[0-9]+", job):
        raise PermissionError("owned job ID required")
    rows = checked(
        "sacct -n -P -X -j " + job + " -o JobID,JobName%128,State%64,AllocCPUS,ElapsedRaw,CPUTimeRAW"
    )
    main = [line.split("|") for line in rows.splitlines() if line.split("|")[0] == job]
    if len(main) != 1:
        return {"state": "ACCOUNTING_PENDING"}
    row = main[0]
    # Slurm appends the cancelling UID, e.g. CANCELLED by 24101. Preserve
    # that evidence separately; lifecycle matching must use the base state.
    state = row[2].strip()
    if re.fullmatch(r"CANCELLED(?: by [0-9]+)?", state):
        state = "CANCELLED"
    # sacct reports AllocCPUS=0 while an owned 64-task request is still queued.
    # Allocation metadata is not the requested task count. This is a valid
    # waiting state, not a foreign job or permission failure.
    pending_without_allocation = state in ("PENDING", "CANCELLED") and int(row[3]) == 0
    if row[1] != binding["job_name"] or (int(row[3]) != 64 and not pending_without_allocation):
        raise PermissionError("scheduler job identity mismatch")
    return {
        "job_id": job,
        "job_name": row[1],
        "state": state,
        "raw_state": row[2],
        "allocated_cpus": int(row[3]),
        "elapsed_seconds": int(row[4]),
        "allocated_core_seconds": int(row[5]),
    }


def cancel(binding):
    row = scheduler(binding)
    if row.get("state") in ("PENDING", "RUNNING", "CONFIGURING", "COMPLETING"):
        checked("scancel " + binding["job_id"])
    return row
