"""Extracted reusable implementation; historical launchers intentionally excluded."""

import hashlib
from pathlib import Path


def plain(path):
    value = Path(path).absolute()
    if ".." in value.parts or any(p.is_symlink() for p in (value, *value.parents)):
        raise PermissionError("owned unlinked recovery path required")
    return value


def file_sha(path):
    return hashlib.sha256(plain(path).read_bytes()).hexdigest()
