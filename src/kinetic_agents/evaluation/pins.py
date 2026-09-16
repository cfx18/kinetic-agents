"""Extracted reusable implementation; historical launchers intentionally excluded."""

import hashlib
from pathlib import Path

PINNED_MODULES = (
    "mechrl/usc_backend.py",
    "mechrl/usc_three_mode.py",
    "mechrl/physical_tools.py",
    "mechrl/structural_actions.py",
    "mechrl/mechanism_closure.py",
    "mechrl/harness/program_feedback.py",
    "mechrl/observable_adapters.py",
    "mechrl/usc_idt_window.py",
)


def source_pins():
    """Keep the existing scientific identities despite Python package relocation."""
    base = Path(__file__).parent / "_frozen"
    paths = {name: base / name for name in PINNED_MODULES}
    paths["mechrl/usc_three_mode.py"] = base / "pins/usc_three_mode.py.txt"
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
