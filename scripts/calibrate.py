#!/usr/bin/env python
"""Judge calibration entry point.

    uv run python scripts/calibrate.py --run runs/<timestamp> --graded-cases id1,id2

Run this only after a human has ticked the criteria in that run's `review.md`.
The logic lives in `regression_detect.calibration` so the test suite can import
and exercise it directly. This file only wires the command line to it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.calibration import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
