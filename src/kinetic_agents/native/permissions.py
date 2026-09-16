"""Native Codex permissions profile: real shell/files, restricted host reads.

Uses the installed CLI's named-profile interface, not unsupported legacy JSON
readOnlyAccess fields. Does not alter user configuration or account credentials.
"""

import json
from pathlib import Path
import subprocess
import tempfile

from kinetic_agents.core.encoding import encoded

NAME = "native-research"
SHELL_ENV = {"PATH": "/usr/bin:/bin", "BASH_ENV": "", "ENV": ""}


def config(work, task):
    return {
        "default_permissions": NAME,
        f"permissions.{NAME}.filesystem": {
            ":root": "deny",
            ":minimal": "read",
            ":tmpdir": "deny",
            ":slash_tmp": "deny",
            str(Path(work).resolve()): "write",
            str(Path(task).resolve()): "read",
        },
        f"permissions.{NAME}.network.enabled": False,
        "shell_environment_policy.inherit": "none",
        # Sanitizing the host environment must not remove basic commands.
        # Fixed values only: do not inherit secrets, user startup scripts,
        # PYTHONPATH, SSH settings, or host-specific executable locations.
        "shell_environment_policy.set": dict(SHELL_ENV),
    }


def toml(value):
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + " = " + toml(v) for k, v in value.items()) + "}"
    return json.dumps(value)


def flags(values):
    return [item for key, value in values.items() for item in ("-c", key + "=" + toml(value))]


def qualify(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError("preserve qualification evidence")
    with tempfile.TemporaryDirectory(
        prefix="native-read-isolation-", dir=output.resolve().parent
    ) as directory:
        root = Path(directory)
        work = root / "work"
        task = root / "task"
        work.mkdir()
        task.mkdir()
        (root / "forbidden").write_text("synthetic private canary")
        (task / "readme").write_text("public input")
        (work / "escape").symlink_to(root / "forbidden")
        source = """import os,json,socket,errno
from pathlib import Path
work,task,private=map(Path,__import__('sys').argv[1:])
assert (task/'readme').read_text()=='public input'
(work/'created').write_text('native filesystem works')
denied=[]
for p in [private,work/'escape',Path('/root/shared-nvme/Caifeixue/AgenticRL/PROJECT_STATUS.md')]:
    try:
        fd=os.open(p,os.O_RDONLY);os.close(fd)
    except OSError as e:
        assert e.errno in (errno.EACCES,errno.EPERM,errno.ENOENT),e
        denied.append(str(p))
    else: raise AssertionError('private path was readable')
try: (task/'modified').write_text('forbidden')
except OSError: pass
else: raise AssertionError('task was writable')
try: socket.create_connection(('1.1.1.1',443),timeout=2)
except OSError as e:
    assert e.errno in (errno.EACCES,errno.EPERM,errno.ENETUNREACH),e
else: raise AssertionError('network was accessible')
import subprocess
ran=subprocess.run(['/bin/bash','-c','command -v python3; command -v cat; python3 -c "print(477)"'],capture_output=True,text=True,timeout=10,
    env={'PATH':'/usr/bin:/bin','BASH_ENV':'','ENV':''})
assert ran.returncode==0 and ran.stdout.splitlines()==['/usr/bin/python3','/usr/bin/cat','477'],ran
print(json.dumps({'private_paths_denied':len(denied),'symlink_denied':True,'task_read_only':True,'work_write':True,'network_denied':True,
                 'fixed_path_commands_work':True}))
"""
        command = [
            "codex",
            *flags(config(work, task)),
            "sandbox",
            "-P",
            NAME,
            "-C",
            str(work),
            "--",
            "/usr/bin/python3",
            "-I",
            "-c",
            source,
            str(work),
            str(task),
            str(root / "forbidden"),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=25)
        receipt = {
            "status": (
                "NATIVE_PERMISSIONS_PASSED"
                if result.returncode == 0
                else "NATIVE_PERMISSIONS_FAILED"
            ),
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:4000],
            "boundary": "native CLI sandbox probe; actual model built-in tools still require a smoke test",
        }
    with output.open("xb") as f:
        f.write(encoded(receipt))
    return receipt


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    args = p.parse_args()
    print(json.dumps(qualify(args.output), indent=2))
