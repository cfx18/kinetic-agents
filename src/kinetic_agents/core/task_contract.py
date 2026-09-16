"""Extracted reusable implementation; historical launchers intentionally excluded."""

import hashlib, json, re
from pathlib import Path
from kinetic_agents.core.encoding import encoded
from kinetic_agents.core.encoding import sha


def validate_reference(reference):
    if (
        type(reference) is not dict
        or set(reference) != {"contract_id", "sha256"}
        or not isinstance(reference["contract_id"], str)
        or not reference["contract_id"].strip()
        or not re.fullmatch("[a-f0-9]{64}", str(reference["sha256"]))
    ):
        raise ValueError("explicit scientific contract_id and original-text sha256 required")
    return dict(reference)


def read_original(path, reference):
    validate_reference(reference)
    if path is None:
        raise ValueError(
            "task_source must be the explicitly selected original text; no default task"
        )
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise PermissionError("regular original task file required")
    data = path.read_bytes()
    if not data.strip() or sha(data) != reference["sha256"]:
        raise PermissionError("scientific task text differs from approved reference")
    data.decode("utf-8")
    return data
