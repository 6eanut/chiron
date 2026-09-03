"""Make the repository root importable so ``import chiron`` works however pytest
is invoked (the CI/pre-push gate runs plain ``pytest -q`` without ``python -m``,
which does not put the repo root on ``sys.path``)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)