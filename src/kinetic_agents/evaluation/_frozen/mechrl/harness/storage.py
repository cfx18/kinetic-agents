"""Local, bounded evidence access and durable side-effect identities."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import re
import uuid


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(encode(value) + '\n')
    tmp.replace(path)


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Archive:
    """Only IDs in this run's store are readable. Never accepts host paths."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, value):
        aid = digest(value)
        target = self.root / (aid + '.json')
        if not target.exists():
            atomic(target, value)
        return aid

    def read(self, artifact_id, offset=0, limit=4000):
        if not re.fullmatch(r'[0-9a-f]{64}', artifact_id):
            raise PermissionError('artifact ID, not a path, is required')
        if offset < 0 or not 1 <= limit <= 8000:
            raise ValueError('invalid character page; limit must be 1..8000')
        path = self.root / (artifact_id + '.json')
        if not path.is_file():
            raise PermissionError('artifact not in this trajectory')
        raw = path.read_text().rstrip('\n')
        if hashlib.sha256(raw.encode()).hexdigest() != artifact_id:
            raise ValueError('artifact integrity mismatch')
        end = min(len(raw), offset + limit)
        return {'artifact_id': artifact_id, 'offset': offset, 'total_chars': len(raw),
                'content': raw[offset:end], 'next_offset': end if end < len(raw) else None}

    def bounded(self, value, limit=10000):
        raw = encode(value)
        aid = self.put(value)
        if len(raw) <= limit:
            return value
        return {'offloaded': True, 'artifact_id': aid, 'total_chars': len(raw),
                'preview': raw[:min(2000, limit // 2)],
                'retrieval': 'read_evidence(artifact_id, offset, limit); no raw data discarded'}

    def load(self, artifact_id):
        """Trusted internal use only. The model is exposed to read(), not load()."""
        self.read(artifact_id, 0, 1)  # validate scope and content hash
        return json.loads((self.root / (artifact_id + '.json')).read_text())
