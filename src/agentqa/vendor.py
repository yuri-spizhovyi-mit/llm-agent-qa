"""Makes the settlement repository's Python packages importable.

`vendor/settlement` (tokenized-settlement-qa v1.0.0) is a submodule, not an installed package:
its `lib` (chain, artifacts, clients) and `transfer_api` live in its `tests/` and `services/`
folders. pytest finds them through `pytest.ini`; every other entry point (the eval CLIs, the
live workflow) finds them because `world.py` calls `ensure_on_path()` first (F-09).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS = (ROOT / "vendor" / "settlement" / "tests", ROOT / "vendor" / "settlement" / "services")


def ensure_on_path() -> None:
    """Idempotent: puts the settlement packages first on sys.path, once."""
    for path in reversed(PATHS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
