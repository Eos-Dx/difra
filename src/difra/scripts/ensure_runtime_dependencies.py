#!/usr/bin/env python3
"""CLI wrapper around standalone DiFRA runtime dependency bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from difra.runtime_deps import main


if __name__ == "__main__":
    raise SystemExit(main())
