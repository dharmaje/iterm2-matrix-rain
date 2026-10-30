"""Run the built-in checks without iTerm2.

    .venv/bin/python tests/run_selftest.py

Same as `matrix-rain selftest` or `python -m matrix_rain.selftest`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matrix_rain.selftest import main  # noqa: E402

raise SystemExit(main())
