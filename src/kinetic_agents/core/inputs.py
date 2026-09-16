"""Resolve one canonical task directory; run directories contain references only."""

import json
from pathlib import Path

from .paths import plain


def task_directory(run_root):
    """Host-owned binding, not an Agent-supplied path or symlink.

    The small fallback supports synthetic fixtures. Production preparations
    always write input-reference.json and pin it in their preflight manifest.
    """
    root = plain(run_root)
    reference = root / "input-reference.json"
    if not reference.exists():
        return plain(root / "task")
    value = json.loads(plain(reference).read_text())
    if set(value) != {"schema", "task_directory"} or value["schema"] != "shared-task.v1":
        raise PermissionError("invalid host task binding")
    path = Path(value["task_directory"])
    if not path.is_absolute():
        raise PermissionError("canonical task binding must be absolute")
    return plain(path)
