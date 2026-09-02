#!/usr/bin/env python
"""Stage 01 entry point.

    uv run python scripts/run_goldens.py --goldens goldens/cases.yaml --samples 1
    uv run python scripts/run_goldens.py --dry-run      # no API key needed

The logic lives in `regression_detect.runner` so the test suite can import and
exercise it directly. This file only wires the command line to it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
