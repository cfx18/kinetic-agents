import base64
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib

from kinetic_agents.team.artifacts import ArtifactArchive
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.service import TeamService
from kinetic_agents.team.store import TeamStore


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cfx-artifacts-")
        self.root = Path(self.tmp.name)
        self.service = TeamService(
            TeamStore(self.root / "host", TeamIdentity("fixture", "a" * 64, "fake", "max"))
        )
        self.service.store.register("pi")
        self.archive = ArtifactArchive(self.service)
        self.metadata = dict(
            evidence_id="raw",
            title="Synthetic raw",
            category="test",
            provenance={"source_id": "fixture", "role": "synthetic"},
            summary="Fixture only",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_bytes_roundtrip_pagination_restart_and_read_only(self):
        raw = "中文 raw\n".encode() + bytes(range(256)) * 80
        evidence = self.archive.ingest(raw, **self.metadata)
        restored = TeamService(TeamStore(self.root / "host"))
        before = restored.store.status()["last_event"]
        offset, parts = 0, []
        while offset is not None:
            page = restored.call(
                "pi", "artifact_read", {"evidence_id": "raw", "offset": offset, "limit": 511}
            )
            parts.append(base64.b64decode(page["content"]))
            offset = page["next_offset"]
        self.assertEqual(b"".join(parts), raw)
        self.assertEqual(restored.store.status()["last_event"], before)
        self.assertEqual(page["sha256"], evidence["artifact_sha256"])

    def test_authorized_file_walk_rejects_symlink_hardlink_fifo_and_traversal(self):
        work = self.root / "work"
        work.mkdir()
        (work / "sub").mkdir()
        (work / "sub/raw").write_bytes(b"data")
        self.archive.ingest_file(work, "sub/raw", **self.metadata)
        (work / "link").symlink_to(work / "sub")
        (work / "leaf").symlink_to(work / "sub/raw")
        os.link(work / "sub/raw", work / "hard")
        os.mkfifo(work / "pipe")
        for name in ("link/raw", "leaf", "hard", "pipe", "../private", "/etc/passwd", "sub//raw"):
            with self.subTest(name=name), self.assertRaises((OSError, ValueError)):
                self.archive.ingest_file(work, name, **self.metadata)

    def test_corruption_not_silently_overwritten_and_foreign_id_denied(self):
        row = self.archive.ingest(b"data", **self.metadata)
        with self.assertRaises(KeyError):
            self.archive.read("pi", evidence_id="foreign")
        with self.assertRaises(PermissionError):
            self.archive.read("foreign", evidence_id="raw")
        path = self.archive.root / row["artifact_sha256"]
        path.chmod(0o600)
        path.write_bytes(b"tamper")
        with self.assertRaises(PermissionError):
            self.archive.put(b"data")

    def test_no_orphan_ingest_can_bypass_evidence_scope_or_final_pool_boundary(self):
        sha = self.archive.put(b"unreferenced after crash")
        with self.assertRaises(KeyError):
            self.archive.read("pi", evidence_id=sha)
        with self.assertRaises(PermissionError):
            self.archive.ingest(
                b"final",
                **{
                    **self.metadata,
                    "provenance": {"source_id": "fixture", "role": "final_evaluation"},
                }
            )

    def test_page_bounds_and_read_of_absent_archive_does_not_create_it(self):
        self.archive.ingest(b"test", **self.metadata)
        for args in (
            {"offset": True},
            {"offset": -1},
            {"offset": 10},
            {"limit": 10000},
            {"limit": False},
        ):
            with self.assertRaises(ValueError):
                self.archive.read("pi", evidence_id="raw", **args)

    def test_repeated_identical_ingest_is_idempotent_after_submission(self):
        first = self.archive.ingest(b"data", **self.metadata)
        self.service.finish("pi", summary="fixture", references=[], request_id="finish")
        self.assertEqual(self.archive.ingest(b"data", **self.metadata), first)
        with self.assertRaises(ValueError):
            self.archive.ingest(b"changed", **self.metadata)

    def test_concurrent_publishers_produce_one_complete_immutable_blob(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            hashes = list(pool.map(lambda _: self.archive.put(b"same" * 1000), range(16)))
        self.assertEqual(len(set(hashes)), 1)
        self.assertEqual(self.archive.load(hashes[0]), b"same" * 1000)

    def test_paged_retrieval_reuses_hash_verification_but_detects_changed_file(self):
        row = self.archive.ingest(b"same" * 1000, **self.metadata)
        with patch("kinetic_agents.team.artifacts.hashlib.sha256", wraps=hashlib.sha256) as h:
            for offset in (0, 20, 40):
                self.service.artifact_read("pi", evidence_id="raw", offset=offset, limit=20)
            self.assertEqual(
                h.call_count, 1, "page reads must not repeatedly hash the entire raw file"
            )
            path = self.archive.root / row["artifact_sha256"]
            path.chmod(0o600)
            path.write_bytes(b"evil" * 1000)
            with self.assertRaises(PermissionError):
                self.service.artifact_read("pi", evidence_id="raw")
            self.assertEqual(h.call_count, 2)


if __name__ == "__main__":
    unittest.main()
