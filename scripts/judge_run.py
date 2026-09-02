#!/usr/bin/env python
"""Stage 02 entry point.

    uv run python scripts/judge_run.py --run runs/<timestamp>
    uv run python scripts/judge_run.py --run runs/<timestamp> --dry-run   # no API key

The logic lives in `regression_detect.judge_runner` so the test suite can import
and exercise it directly. This file only wires the command line to it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.judge_runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
