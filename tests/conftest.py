"""Pytest config: ensure src/ is on sys.path so `albra_seed` imports work
without an editable install of the package layout (which lives in src/)."""

from __future__ import annotations

import sys
from pathlib import Path

# conftest.py lives at <root>/tests/conftest.py; src/ is sibling of tests/.
#   tests/conftest.py -> parents[0]=tests, parents[1]=<root>, then + "src".
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
