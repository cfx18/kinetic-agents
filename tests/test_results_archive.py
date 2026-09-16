import hashlib
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("verify_results_archive", Path(__file__).resolve().parents[1] / "scripts/verify_results_archive.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_manifest_roundtrip_and_mutation(tmp_path):
    p = tmp_path / "score.json"
    p.write_bytes(b'{"status":"failure"}')
    row = {"path": p.name, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    assert verifier.verify_file_manifest(tmp_path, [row]) == 1
    p.write_bytes(b'{"status":"success"}')
    with pytest.raises(ValueError):
        verifier.verify_file_manifest(tmp_path, [row])


def test_duplicate_manifest(tmp_path):
    p = tmp_path / "a"; p.write_bytes(b"a")
    row = {"path": "a", "bytes": 1, "sha256": verifier.digest(p)}
    with pytest.raises(ValueError):
        verifier.verify_file_manifest(tmp_path, [row, row])


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", "", ".", "a/../b", "a\\b", "a//b"])
def test_unsafe_paths(tmp_path, path):
    with pytest.raises(ValueError):
        verifier.safe_file(tmp_path, path)


def test_symlink_to_inside_is_rejected(tmp_path):
    (tmp_path / "real").write_text("evidence")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError):
        verifier.safe_file(tmp_path, "link")


def test_missing_and_success_not_interchangeable():
    with pytest.raises(ValueError):
        verifier.assert_same({"full_mean": 0.0}, {"full_mean": None})
    with pytest.raises(ValueError):
        verifier.assert_same({"count": True}, {"count": 1})
    verifier.assert_same({"value": 0.3}, {"value": 0.1 + 0.2})
