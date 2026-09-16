"""Host-owned, per-run, content-addressed raw bytes; not an execution sandbox.

The caller supplies bytes or a path relative to an explicitly authorized work
directory, never an arbitrary host path. File I/O and hashing happen outside
SQLite transactions. Unreferenced blobs after a crash are harmless and retained.
"""

import base64
from collections import OrderedDict
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading


class ArtifactArchive:
    MAX_BYTES = 32 * 1024 * 1024

    def __init__(self, service, *, create=True):
        self.service = service
        self.verified = OrderedDict()
        self._verified_lock = threading.Lock()
        self.root = service.store.root / "artifacts"
        service.store._check_paths()
        if self.root.is_symlink():
            raise PermissionError("linked artifact archive")
        if create:
            self.root.mkdir(mode=0o700, exist_ok=True)
        elif not self.root.is_dir():
            raise FileNotFoundError("no raw artifacts have been ingested in this run")

    @staticmethod
    def _sha(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("artifact SHA-256 required, not a path")
        return value

    def _open(self, name):
        self.service.store._check_paths()
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)

    @contextmanager
    def _lock(self, *, write=False):
        self.service.store._check_paths()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            yield fd
        finally:
            os.close(fd)

    @classmethod
    def _bytes(cls, fd):
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("artifact must be a regular file without hard links")
            if info.st_size > cls.MAX_BYTES:
                raise ValueError("artifact exceeds archive limit")
            data = stream.read(cls.MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (
                len(data) > cls.MAX_BYTES
                or info.st_size != after.st_size
                or info.st_mtime_ns != after.st_mtime_ns
                or info.st_ctime_ns != after.st_ctime_ns
            ):
                raise ValueError("artifact changed during ingestion or exceeds limit")
            return data

    def put(self, data):
        if not isinstance(data, bytes) or len(data) > self.MAX_BYTES:
            raise ValueError("bounded raw bytes required")
        sha = hashlib.sha256(data).hexdigest()
        self.service.store._check_paths()
        if self.root.is_symlink():
            raise PermissionError("linked artifact archive")
        # A short FILE lock (not SQLite) serializes same-run publishers. Rename
        # publishes a complete single-link file; no partially written target or
        # transient hard link can confuse readers or crash recovery.
        with self._lock(write=True) as directory:
            target = self.root / sha
            if target.exists() or target.is_symlink():
                if self._load(sha) != data:
                    raise PermissionError("existing archive content differs from its hash")
                return sha
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o400)
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                os.fsync(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return sha

    def load(self, sha):
        """Trusted host only; agent retrieval is bounded and evidence-scoped."""
        with self._lock():
            return self._load(sha)

    def _load(self, sha):
        data = self._bytes(self._open(self._sha(sha)))
        if hashlib.sha256(data).hexdigest() != sha:
            raise PermissionError("raw artifact integrity mismatch")
        return data

    def ingest(self, data, **metadata):
        sha = self.put(data)
        return self.service.import_evidence(artifact_sha256=sha, **metadata)

    def ingest_file(self, work, relative, **metadata):
        """Walk via directory FDs, rejecting symlinks at every level (incl. FIFO)."""
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or "\\" in relative
        ):
            raise PermissionError("relative work-file path required")
        work = Path(work).absolute()
        if any(p.is_symlink() for p in (work, *work.parents)):
            raise PermissionError("linked work directory")
        current = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = relative.split("/")
            for name in parts[:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
            data = self._bytes(fd)
        finally:
            os.close(current)
        return self.ingest(data, **metadata)

    def read(self, actor, *, evidence_id, offset=0, limit=4096):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 8192
        ):
            raise ValueError("byte page limit must be 1..8192")
        evidence = self.service.read(actor, kind="evidence", record_id=evidence_id)
        sha = self._sha(evidence["artifact_sha256"])
        with self._lock(), os.fdopen(self._open(sha), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > self.MAX_BYTES
            ):
                raise PermissionError("invalid raw artifact file")

            def signature(row):
                return (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)

            pinned = signature(info)
            # Immutable HOST-owned files: verify once per file identity, then
            # seek only the requested page. Do not rehash a 32MiB result for
            # every 4KiB page. The bounded cache holds metadata, never raw bytes.
            with self._verified_lock:
                verified = self.verified.get(sha) == pinned
            if not verified:
                h = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                if h.hexdigest() != sha:
                    raise PermissionError("raw artifact integrity mismatch")
            if offset > info.st_size:
                raise ValueError("offset exceeds artifact length")
            stream.seek(offset)
            data = stream.read(limit)
            if signature(os.fstat(stream.fileno())) != pinned:
                raise PermissionError("raw artifact changed while being read")
            with self._verified_lock:
                self.verified[sha] = pinned
                self.verified.move_to_end(sha)
                if len(self.verified) > 128:
                    self.verified.popitem(last=False)
        end = offset + len(data)
        return {
            "evidence_id": evidence_id,
            "sha256": sha,
            "offset": offset,
            "total_bytes": info.st_size,
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
            "next_offset": end if end < info.st_size else None,
        }
