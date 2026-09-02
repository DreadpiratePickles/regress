#!/usr/bin/env python
"""Stage 03 entry point.

    uv run python scripts/compare.py --baseline baselines/summarizer/baseline.json \
        --candidate runs/<timestamp>

Exit code is the verdict: 0 NO_REGRESSION, 1 REGRESSION, 2 INCONCLUSIVE,
3 a bad input. The logic lives in `regression_detect.compare_run` so the test
suite can import and exercise it directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.compare_run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
