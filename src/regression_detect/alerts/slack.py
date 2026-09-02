"""Build and post the Slack message a REGRESSION verdict earns.

This is the only place in the tool that sends anything to a third party, so the
constraints are deliberately narrow:

- the payload is built from a finished comparison, never from a model;
- the body is bounded before it is sent, so a pathological golden set cannot
  turn one bad run into a 5 MB POST;
- every failure is a typed error, and no error message, log line or exception
  ever carries the webhook URL — the URL *is* the credential;
- the caller supplies the transport, so every test runs without a socket.

Whether to send at all is not decided here. That gate lives in `alert_run.py`.
"""

import json
from collections.abc import Callable
from typing import Any

WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"
"""Name only. The value is a secret and lives in the environment, never in a
committed file, a log line or an error message."""

TIMEOUT_SECONDS = 10.0
MAX_BODY_BYTES = 40_000
MAX_LISTED_CRITERIA = 5
MAX_CRITERION_CHARS = 160
MAX_EXPLANATION_CHARS = 1_500
MAX_RESPONSE_EXCERPT = 200

BADGES = {
    "REGRESSION": "🔴 REGRESSION",
    "NO_REGRESSION": "🟢 NO_REGRESSION",
    "INCONCLUSIVE": "🟡 INCONCLUSIVE",
}

Transport = Callable[..., tuple[int, str]]
"""`transport(*, url: str, body: bytes, timeout: float) -> (status, body)`."""


class AlertError(Exception):
    """An alert could not be built or delivered."""


class AlertConfigError(AlertError):
    """The webhook or the payload is unusable; nothing was sent."""


class AlertTransportError(AlertError):
    """The request could not be completed: DNS, connection, or timeout."""


class AlertResponseError(AlertError):
    """The endpoint answered, and the answer was not a success."""


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _rate(side: dict[str, Any]) -> str:
    return f"{side['passes']}/{side['n']}"


def _drop(row: dict[str, Any]) -> float:
    difference = row.get("difference")
    return 0.0 if difference is None else float(difference)


def _worsened(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in comparison.get("criteria", [])
        if row.get("hard_regression") or _drop(row) < 0
    ]
    return sorted(rows, key=lambda row: (_drop(row), row["case_id"], row["criterion_index"]))


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _criteria_block(comparison: dict[str, Any]) -> dict[str, Any] | None:
    worsened = _worsened(comparison)
    if not worsened:
        return None

    lines = ["*Criteria that got worse*"]
    for row in worsened[:MAX_LISTED_CRITERIA]:
        mark = " ‼️" if row.get("hard_regression") else ""
        lines.append(
            f"• `{row['case_id']}` [{row['criterion_index'] + 1}] "
            f"{_truncate(row['criterion'], MAX_CRITERION_CHARS)} — "
            f"{_rate(row['baseline'])} → {_rate(row['candidate'])}{mark}"
        )
    remaining = len(worsened) - MAX_LISTED_CRITERIA
    if remaining > 0:
        lines.append(f"_…and {remaining} more; see the report._")
    return _section("\n".join(lines))


def build_slack_payload(
    comparison: dict[str, Any], provenance: Any, report_url: str | None
) -> dict[str, Any]:
    """Build the Block Kit payload for one finished comparison.

    Args:
        comparison: the `comparison.json` payload stage 03 wrote.
        provenance: the run's `Provenance`, for the context line.
        report_url: where the full report can be read, or `None` to omit the link.

    Nothing is decided here: the verdict, the sentence and the numbers all come
    from the comparison as stage 03 recorded them.
    """
    verdict = comparison["verdict"]
    badge = BADGES.get(verdict, verdict)
    baseline = ", ".join(f"`{run_id}`" for run_id in provenance.baseline_run_ids) or "—"

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": badge, "emoji": True}},
        _section(_truncate(comparison["explanation"], MAX_EXPLANATION_CHARS)),
    ]
    criteria = _criteria_block(comparison)
    if criteria is not None:
        blocks.append(criteria)
    if report_url:
        blocks.append(_section(f"<{report_url}|Full report>"))
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Run `{provenance.run_id}` · target `{provenance.target_model_id}` · "
                        f"judge `{provenance.judge_model_id}` · baseline {baseline}"
                    ),
                }
            ],
        }
    )

    return {"text": f"{badge} — regression check for `{provenance.run_id}`", "blocks": blocks}


def _validated_webhook(webhook_url: Any) -> str:
    if not isinstance(webhook_url, str) or not webhook_url.strip():
        raise AlertConfigError(
            f"No Slack webhook was given. Set {WEBHOOK_ENV_VAR} in the environment."
        )
    webhook_url = webhook_url.strip()
    if not webhook_url.startswith("https://"):
        raise AlertConfigError(
            f"{WEBHOOK_ENV_VAR} must be an https:// URL; a webhook carries a credential "
            "and must not travel in the clear."
        )
    return webhook_url


def default_transport(*, url: str, body: bytes, timeout: float) -> tuple[int, str]:
    """POST `body` as JSON with `urllib`. The URL never reaches an error message."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(  # noqa: S310 — https is enforced by the caller
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read(MAX_RESPONSE_EXCERPT).decode(
                "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_RESPONSE_EXCERPT).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AlertTransportError(
            f"The Slack webhook could not be reached ({type(exc).__name__})."
        ) from exc


def send_slack(
    payload: dict[str, Any], webhook_url: str, *, transport: Transport | None = None
) -> str:
    """POST `payload` to `webhook_url` and return the response body.

    Raises:
        AlertConfigError: if the webhook is missing or not an https URL, or the
            serialised payload exceeds `MAX_BODY_BYTES`. Nothing is sent.
        AlertTransportError: if the request could not be completed.
        AlertResponseError: if the endpoint answered with a non-success status.
    """
    url = _validated_webhook(webhook_url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise AlertConfigError(
            f"The Slack payload is {len(body)} bytes, above the {MAX_BODY_BYTES}-byte "
            "bound. Nothing was sent; link the report instead of inlining it."
        )

    send = transport or default_transport
    try:
        status, response = send(url=url, body=body, timeout=TIMEOUT_SECONDS)
    except AlertError:
        raise
    except (OSError, ValueError) as exc:
        raise AlertTransportError(
            f"The Slack webhook could not be reached ({type(exc).__name__})."
        ) from exc

    if not 200 <= status < 300:
        raise AlertResponseError(
            f"The Slack webhook answered {status}: "
            f"{_truncate(response, MAX_RESPONSE_EXCERPT)}"
        )
    return response
