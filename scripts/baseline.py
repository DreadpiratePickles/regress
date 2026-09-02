#!/usr/bin/env python
"""Baseline entry point.

    uv run python scripts/baseline.py build --runs runs/A runs/B \
        --out baselines/summarizer/baseline.json
    uv run python scripts/baseline.py show --baseline baselines/summarizer/baseline.json

The logic lives in `regression_detect.baseline` so the test suite can import and
exercise it directly. This file only wires the command line to it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.baseline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
