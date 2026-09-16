import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import pytest

pytestmark=pytest.mark.skipif(shutil.which("zstd") is None, reason="Archive CLI tests require zstd")

MODULE=Path(__file__).resolve().parents[1]/"scripts/restore_release.py"
spec=importlib.util.spec_from_file_location("restore_release",MODULE)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def make_asset(tmp_path, member="runs/r1/transcript.md", content=b"public evidence", symlink=False):
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode="w") as t:
        info=tarfile.TarInfo(member)
        if symlink:
            info.type=tarfile.SYMTYPE;info.linkname="../../escape";t.addfile(info)
        else:
            info.size=len(content);t.addfile(info,io.BytesIO(content))
    compressed=subprocess.run(["zstd","-q","-c"],input=raw.getvalue(),capture_output=True,check=True).stdout
    assets=tmp_path/"assets";assets.mkdir()
    name="r1.part001.tar.zst";(assets/name).write_bytes(compressed)
    archive=tmp_path/"archive";archive.mkdir()
    row={"path":member,"asset":name,"bytes":len(content),"sha256":hashlib.sha256(content).hexdigest()}
    files=archive/"files.jsonl";files.write_text(json.dumps(row)+"\n")
    index=archive/"assets.json";index.write_text(json.dumps({"files_manifest":"files.jsonl","files_manifest_sha256":mod.digest(files),
        "assets":[{"name":name,"bytes":len(compressed),"sha256":hashlib.sha256(compressed).hexdigest(),"files":1,"run_id":"r1"}]}))
    return index,assets

def test_roundtrip(tmp_path):
    index,assets=make_asset(tmp_path)
    result=mod.restore(index,assets,tmp_path/"restored")
    assert result["files"]==1
    assert (tmp_path/"restored/runs/r1/transcript.md").read_bytes()==b"public evidence"

def test_no_overwrite(tmp_path):
    index,assets=make_asset(tmp_path)
    out=tmp_path/"existing";out.mkdir();(out/"keep").write_text("user")
    with pytest.raises(FileExistsError):mod.restore(index,assets,out)
    assert (out/"keep").read_text()=="user"

def test_asset_tampering_before_output(tmp_path):
    index,assets=make_asset(tmp_path)
    with next(assets.iterdir()).open("ab") as stream:stream.write(b"tamper")
    with pytest.raises(ValueError):mod.restore(index,assets,tmp_path/"out")
    assert not (tmp_path/"out").exists()

def test_traversal(tmp_path):
    index,assets=make_asset(tmp_path,"../escape")
    with pytest.raises(ValueError):mod.restore(index,assets,tmp_path/"out")
    assert not (tmp_path/"out").exists()

def test_symlink_member(tmp_path):
    index,assets=make_asset(tmp_path,symlink=True)
    with pytest.raises(ValueError):mod.restore(index,assets,tmp_path/"out")
    assert not (tmp_path/"escape").exists()

def test_verify_only(tmp_path):
    index,assets=make_asset(tmp_path)
    assert mod.restore(index,assets,verify_only=True)["status"]=="verified"
    with pytest.raises(ValueError):mod.restore(index,assets,run="other",verify_only=True)

