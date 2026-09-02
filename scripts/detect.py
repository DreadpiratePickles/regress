#!/usr/bin/env python
"""Whole-pipeline entry point: run, judge, compare, in that order.

    uv run python scripts/detect.py --baseline baselines/summarizer/baseline.json \
        --samples 1 --min-interval-ms 6500
    uv run python scripts/detect.py --baseline <path> --dry-run   # no API key

Exit code is the comparison's: 0 NO_REGRESSION, 1 REGRESSION, 2 INCONCLUSIVE,
3 a bad input. The logic lives in `regression_detect.pipeline` so the test suite
can import and exercise it directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.pipeline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
