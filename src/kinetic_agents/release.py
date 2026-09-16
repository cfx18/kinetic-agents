"""Freeze only this installable package, never workspace history or local inputs."""

import hashlib
import json
import os
from pathlib import Path
import stat

SOURCE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release-manifest.json"
SCHEMA = "kinetic-agents-source.v1"


def encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def plain(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise PermissionError("plain source paths required")
    return path


def regular(path):
    path = plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("source must be a regular single-link file")
    return path


def inventory(root, *, strict=False):
    base = plain(root) / "kinetic_agents"
    if not base.is_dir():
        raise FileNotFoundError("installed kinetic_agents source package missing")
    names = []
    for directory, children, files in os.walk(base, followlinks=False):
        if strict and "__pycache__" in children:
            raise PermissionError("bytecode caches are forbidden in a frozen release")
        children[:] = sorted(n for n in children if n != "__pycache__")
        for name in children:
            plain(Path(directory) / name)
        for name in sorted(files):
            p = regular(Path(directory) / name)
            relative = p.relative_to(root).as_posix()
            if (
                p.suffix != ".py"
                and relative != "kinetic_agents/evaluation/_frozen/pins/usc_three_mode.py.txt"
            ):
                raise PermissionError("unexpected source file: " + relative)
            names.append(relative)
    return sorted(names)


def build(destination):
    destination = plain(destination)
    if destination.exists():
        raise FileExistsError("source release already exists; never overwrite")
    if destination.is_relative_to(SOURCE_ROOT / "kinetic_agents"):
        raise PermissionError("release cannot be nested in installed source")
    names = inventory(SOURCE_ROOT)
    blobs = {n: regular(SOURCE_ROOT / n).read_bytes() for n in names}
    files = {n: digest(b) for n, b in blobs.items()}
    core = {
        "schema": SCHEMA,
        "files": files,
        "host_only": True,
        "contains_experimental_data": False,
        "contains_credentials": False,
    }
    manifest = {**core, "release_sha256": digest(encoded(core))}
    destination.mkdir(mode=0o700, parents=True)
    for name, blob in blobs.items():
        p = destination / name
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with p.open("xb") as stream:
            stream.write(blob)
    if inventory(SOURCE_ROOT) != names or any(
        digest(regular(SOURCE_ROOT / n).read_bytes()) != h for n, h in files.items()
    ):
        raise RuntimeError("source changed during freeze; partial release cannot launch")
    with (destination / MANIFEST).open("xb") as stream:
        stream.write(encoded(manifest) + b"\n")
    for p in destination.rglob("*"):
        p.chmod(0o500 if p.is_dir() else 0o400)
    destination.chmod(0o500)
    return verify(destination)


def verify(directory):
    directory = plain(directory)
    if {p.name for p in directory.iterdir()} != {"kinetic_agents", MANIFEST}:
        raise PermissionError("unexpected top-level frozen release file")
    manifest = json.loads(regular(directory / MANIFEST).read_bytes())
    core = {k: v for k, v in manifest.items() if k != "release_sha256"}
    if (
        set(core)
        != {"schema", "files", "host_only", "contains_experimental_data", "contains_credentials"}
        or core["schema"] != SCHEMA
        or core["host_only"] is not True
        or core["contains_experimental_data"] is not False
        or core["contains_credentials"] is not False
        or digest(encoded(core)) != manifest.get("release_sha256")
    ):
        raise PermissionError("invalid frozen release manifest")
    if not isinstance(core["files"], dict) or sorted(core["files"]) != inventory(
        directory, strict=True
    ):
        raise PermissionError("source inventory differs from locked release")
    if any(digest(regular(directory / n).read_bytes()) != h for n, h in core["files"].items()):
        raise PermissionError("frozen source changed")
    return manifest
