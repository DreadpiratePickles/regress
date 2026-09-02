#!/usr/bin/env python
"""Stage 04 entry point: render a compared run into a pull-request comment.

    uv run python scripts/report.py --run runs/<timestamp>
    uv run python scripts/report.py --run runs/<timestamp> --out report.md

Writes `report.md` into the run directory unless `--out` says otherwise. Exit
code is 0, or 3 when the run cannot be read. Nothing here calls a model. The
logic lives in `regression_detect.report` so the test suite can import and
exercise it directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.report import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
