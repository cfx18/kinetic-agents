"""Source-tree entrypoint; installed CLI uses the identical implementation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from kinetic_agents.cli import main

if __name__ == "__main__":
    sys.exit(main())
