#!/usr/bin/env python
"""Stage 04 entry point: post a compared run's verdict to Slack.

    uv run python scripts/alert.py --run runs/<timestamp>            # dry run
    uv run python scripts/alert.py --run runs/<timestamp> --send     # posts

The default prints the payload and sends nothing. `--send` posts only when
`SLACK_WEBHOOK_URL` is set and only for a REGRESSION verdict, unless `--always`
is given. Exit code is 0 sent or deliberately skipped, 1 the send failed,
2 misconfigured, 3 the run could not be read. The logic lives in
`regression_detect.alert_run` so the test suite can import and exercise it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_detect.alert_run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
