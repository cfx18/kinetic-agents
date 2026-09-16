"""Process-local pyMARS logging location; never changes the shared installation.

pyMARS 1.2.0 eagerly constructs FileHandler('info.log') at import. Preload it
from this execution's host-owned log directory before scientific entrypoints.
Scientific source, solver, graph implementation and its source pins stay intact.
"""

import os
from pathlib import Path
import sys

try:
    directory = Path(os.environ["CFX_SCIENCE_LOG_DIR"])
    if (
        not directory.is_absolute()
        or directory.is_symlink()
        or not directory.is_dir()
        or any(p.is_symlink() for p in directory.parents)
    ):
        raise RuntimeError("owned plain log directory required")
    previous = os.getcwd()
    try:
        os.chdir(directory)
        import pymars
    finally:
        os.chdir(previous)
except BaseException:
    sys.stderr.write("scientific logging bootstrap failed; execution refused\n")
    raise SystemExit(78)
