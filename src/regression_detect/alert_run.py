"""Stage 04's alert gate: decide whether a Slack message is sent, and send it.

Sending a message is the only thing this tool does that leaves the machine, so
it is gated three times over and the default is to do nothing:

1. **Dry run by default.** Without `--send` the payload is printed and no
   request is made. That is what a reviewer reads before the first real alert.
2. **A secret must be present.** `--send` without `SLACK_WEBHOOK_URL` exits 2
   with a message naming the variable — never its value, which is a credential.
3. **Only a regression alerts.** A clean or inconclusive run is not worth
   waking anybody for, unless `--always` says otherwise.

Building the payload and posting it live in `alerts/slack.py`; the decision
about whether to post lives here, in deterministic code with no model in it.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .alerts import slack
from .alerts.slack import (
    WEBHOOK_ENV_VAR,
    AlertConfigError,
    AlertError,
    Transport,
    build_slack_payload,
    send_slack,
)
from .comparison import INPUT_ERROR_EXIT_CODE
from .report_inputs import ReportInputError, read_comparison, read_provenance

ALERT_VERDICT = "REGRESSION"
"""The one verdict worth an unprompted message. `--always` overrides it."""

CONFIG_EXIT_CODE = 2
SEND_FAILED_EXIT_CODE = 1


def default_transport() -> Transport:
    """The transport a real send goes through. A seam the tests replace."""
    return slack.default_transport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alert",
        description="Stage 04: post a compared run's verdict to Slack. Dry run by default.",
    )
    parser.add_argument(
        "--run", type=Path, required=True, help="A run directory holding comparison.json."
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help=f"Actually post. Requires {WEBHOOK_ENV_VAR} in the environment.",
    )
    parser.add_argument(
        "--always",
        action="store_true",
        help="Post for every verdict, not only REGRESSION.",
    )
    parser.add_argument(
        "--report-url",
        default=None,
        help="Where the full report can be read; linked from the message when given.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. 0 sent or skipped, 1 send failed, 2 misconfigured, 3 bad run."""
    args = build_parser().parse_args(argv)

    try:
        comparison = read_comparison(args.run)
        provenance = read_provenance(args.run, comparison)
    except ReportInputError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return INPUT_ERROR_EXIT_CODE

    payload = build_slack_payload(comparison, provenance, args.report_url)

    if not args.send:
        print("DRY RUN — nothing was sent. The payload that --send would post:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    verdict = comparison["verdict"]
    if verdict != ALERT_VERDICT and not args.always:
        print(f"Verdict is {verdict}; no alert sent. Pass --always to post anyway.")
        return 0

    webhook = os.environ.get(WEBHOOK_ENV_VAR, "")
    if not webhook.strip():
        print(
            f"{WEBHOOK_ENV_VAR} is not set, so --send has nothing to post to. Set it in the "
            "environment (in CI, as a repository secret) or drop --send to dry-run.",
            file=sys.stderr,
        )
        return CONFIG_EXIT_CODE

    try:
        send_slack(payload, webhook, transport=default_transport())
    except AlertConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return CONFIG_EXIT_CODE
    except AlertError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return SEND_FAILED_EXIT_CODE

    print(f"Alert sent for {verdict} on run {provenance.run_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
