"""Verify and restore downloaded private research assets. No model or network calls."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

CHUNK = 1024 * 1024

def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(CHUNK):
            value.update(block)
    return value.hexdigest()

def safe_relative(value):
    path = PurePosixPath(value)
    if not value or value == "." or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ValueError("Unsafe archive path")
    if str(path) != value:
        raise ValueError("Noncanonical archive path")
    return path

def restore(index_path, assets_dir, output=None, run=None, verify_only=False):
    index_path = Path(index_path).resolve()
    root = Path(assets_dir).resolve()
    index = json.loads(index_path.read_text())
    manifest_path = index_path.parent / safe_relative(index["files_manifest"])
    if digest(manifest_path) != index["files_manifest_sha256"]:
        raise ValueError("File manifest hash mismatch")
    selected = [a for a in index["assets"] if run is None or a["run_id"] == run]
    if not selected:
        raise ValueError("No assets match this run")
    names = [a["name"] for a in selected]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate asset names")
    for name in names:
        path = safe_relative(name)
        if len(path.parts) != 1:
            raise ValueError("Asset filename must be a basename")
    expected = {name:{} for name in names}
    globally_seen = set()
    with manifest_path.open() as stream:
        for line in stream:
            row = json.loads(line)
            name = row.get("asset")
            if name not in expected:
                continue
            path = str(safe_relative(row["path"]))
            if path in globally_seen:
                raise ValueError("Duplicate manifest target")
            globally_seen.add(path)
            expected[name][path] = row
    # Verify ALL compressed files before creating any output directory.
    for asset in selected:
        path = root / asset["name"]
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError("Asset is a symlink or escapes the download directory")
        if path.stat().st_size != asset["bytes"] or digest(path) != asset["sha256"]:
            raise ValueError("Asset hash/size mismatch: " + asset["name"])
        if len(expected[asset["name"]]) != asset["files"]:
            raise ValueError("Asset member count differs from manifest")
    destination = None
    if not verify_only:
        if output is None:
            raise ValueError("--output is required unless --verify-only")
        requested = Path(output).absolute()
        if requested.is_symlink() or requested.exists():
            raise FileExistsError("Refusing to overwrite output or follow its symlink")
        destination = requested.resolve()
        if destination == root or root.is_relative_to(destination):
            raise ValueError("Output must not contain the input assets")
        destination.mkdir(parents=True)
        (destination/"RESTORE_INCOMPLETE.json").write_text(json.dumps({"status":"incomplete","run":run})+"\n")
    count = 0
    total = 0
    for asset in selected:
        seen = set()
        with tempfile.TemporaryFile() as err:
            process = subprocess.Popen(["zstd","--decompress","--stdout","--quiet","--",str(root/asset["name"])], stdout=subprocess.PIPE, stderr=err)
            try:
                with tarfile.open(fileobj=process.stdout, mode="r|") as bundle:
                    for member in bundle:
                        relative = str(safe_relative(member.name))
                        if not member.isfile() or relative not in expected[asset["name"]] or relative in seen:
                            raise ValueError("Unexpected, nonregular or duplicate archive member")
                        row = expected[asset["name"]][relative]
                        if member.size != row["bytes"]:
                            raise ValueError("Uncompressed member size mismatch")
                        target = None
                        if destination:
                            target = destination / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if target.parent.resolve() != target.parent or not target.parent.is_relative_to(destination):
                                raise ValueError("Output parent escapes destination")
                        value = hashlib.sha256()
                        length = 0
                        saved = target.open("xb") if target else None
                        try:
                            with bundle.extractfile(member) as source:
                                while block := source.read(CHUNK):
                                    value.update(block)
                                    length += len(block)
                                    if saved:
                                        saved.write(block)
                        finally:
                            if saved:
                                saved.close()
                        if length != row["bytes"] or value.hexdigest() != row["sha256"]:
                            raise ValueError("Restored file hash/length mismatch")
                        seen.add(relative)
                        count += 1
                        total += length
                # Drain trailing compressed frames so zstd can report corruption.
                while process.stdout.read(CHUNK):
                    pass
                process.stdout.close()
                if process.wait(timeout=30):
                    raise ValueError("zstd decompression failed")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=30)
                if process.stdout:
                    process.stdout.close()
        if seen != set(expected[asset["name"]]):
            raise ValueError("Archive omitted manifest-listed files")
    result = {"status":"verified" if verify_only else "restored", "assets":len(selected),"files":count,"bytes":total,"run":run,
              "output":str(destination) if destination else None,"network_calls":0,"model_calls":0}
    if destination:
        (destination/"RESTORE_COMPLETE.json").write_text(json.dumps(result,indent=2)+"\n")
        (destination/"RESTORE_INCOMPLETE.json").unlink()
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index",type=Path,default=Path(__file__).resolve().parents[1]/"archive/assets.json")
    parser.add_argument("--assets",type=Path,required=True)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--run")
    parser.add_argument("--verify-only",action="store_true")
    args=parser.parse_args()
    print(json.dumps(restore(args.index,args.assets,args.output,args.run,args.verify_only),indent=2))
