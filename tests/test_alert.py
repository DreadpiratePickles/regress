"""Tests for stage 04's Slack alert: the payload, the sender, and the CLI gate.

No test opens a socket. `send_slack` takes a transport, and every test here
passes a fake one that records what it was handed. The CLI tests set and unset
`SLACK_WEBHOOK_URL` through monkeypatch; the value used is a placeholder that
belongs to nobody.
"""

import io
import json
from pathlib import Path

import pytest

from regression_detect import alert_run
from regression_detect.alerts import slack
from regression_detect.alerts.slack import (
    MAX_BODY_BYTES,
    WEBHOOK_ENV_VAR,
    AlertConfigError,
    AlertResponseError,
    AlertTransportError,
    build_slack_payload,
    send_slack,
)
from regression_detect.report_inputs import Provenance
from test_report import comparison_payload, criterion_row
from test_report_inputs import build_run_dir

PLACEHOLDER_WEBHOOK = "https://hooks.example.invalid/services/PLACEHOLDER"


class FakeTransport:
    """Records the one request it is given and replies with a canned response."""

    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status = status
        self.body = body
        self.calls: list[dict] = []

    def __call__(self, *, url: str, body: bytes, timeout: float) -> tuple[int, str]:
        self.calls.append({"url": url, "body": body, "timeout": timeout})
        return self.status, self.body


def provenance() -> Provenance:
    return Provenance(
        run_id="2026-09-02T10-00-00Z",
        target_model_id="target-model",
        judge_model_id="judge-model",
        prompt_sha256="a" * 64,
        judge_prompt_sha256="b" * 64,
        goldens_sha256="c" * 64,
        samples=1,
        baseline_run_ids=("2026-01-01T00-00-00Z",),
    )


def payload_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------
# the payload
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "badge"),
    [
        ("REGRESSION", "🔴 REGRESSION"),
        ("NO_REGRESSION", "🟢 NO_REGRESSION"),
        ("INCONCLUSIVE", "🟡 INCONCLUSIVE"),
    ],
)
def test_the_header_block_carries_the_verdict(verdict: str, badge: str) -> None:
    payload = build_slack_payload(comparison_payload(verdict=verdict), provenance(), None)

    header = payload["blocks"][0]
    assert header["type"] == "header"
    assert badge in header["text"]["text"]
    assert badge in payload["text"]


def test_the_explanation_is_carried_verbatim() -> None:
    sentence = "Pass rate fell from 100.0% to 60.0%; p = 0.0044 → REGRESSION."
    payload = build_slack_payload(
        comparison_payload(explanation=sentence), provenance(), None
    )

    assert sentence in payload_text(payload)


def test_the_worsened_criteria_are_listed_worst_first() -> None:
    criteria = [
        criterion_row("a", 0, text="small drop", baseline=(4, 4), candidate=(3, 4)),
        criterion_row("b", 0, text="big drop", baseline=(4, 4), candidate=(0, 4)),
    ]
    text = payload_text(build_slack_payload(comparison_payload(criteria=criteria),
                                            provenance(), None))

    assert text.index("big drop") < text.index("small drop")


def test_at_most_five_worsened_criteria_are_listed() -> None:
    criteria = [
        criterion_row(f"case{index}", 0, text=f"drop {index}", baseline=(8, 8),
                      candidate=(index, 8))
        for index in range(8)
    ]
    text = payload_text(
        build_slack_payload(comparison_payload(criteria=criteria), provenance(), None)
    )

    listed = [index for index in range(8) if f"drop {index}" in text]
    assert len(listed) == 5
    assert "3 more" in text


def test_the_provenance_line_names_the_run_and_both_models() -> None:
    text = payload_text(build_slack_payload(comparison_payload(), provenance(), None))

    assert "2026-09-02T10-00-00Z" in text
    assert "target-model" in text
    assert "judge-model" in text


def test_a_report_url_becomes_a_link_and_is_omitted_when_absent() -> None:
    with_url = payload_text(
        build_slack_payload(comparison_payload(), provenance(), "https://example.com/pr/1")
    )
    without_url = payload_text(build_slack_payload(comparison_payload(), provenance(), None))

    assert "https://example.com/pr/1" in with_url
    assert "example.com" not in without_url


def test_the_payload_stays_inside_the_body_bound() -> None:
    criteria = [
        criterion_row(f"case{index}", 0, text="x" * 4000, baseline=(4, 4), candidate=(0, 4))
        for index in range(20)
    ]
    payload = build_slack_payload(comparison_payload(criteria=criteria), provenance(), None)

    assert len(json.dumps(payload).encode("utf-8")) <= MAX_BODY_BYTES


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------


def test_send_posts_the_payload_as_json_with_a_timeout() -> None:
    transport = FakeTransport()
    payload = build_slack_payload(comparison_payload(), provenance(), None)

    send_slack(payload, PLACEHOLDER_WEBHOOK, transport=transport)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == PLACEHOLDER_WEBHOOK
    assert json.loads(call["body"].decode("utf-8")) == payload
    assert call["timeout"] == 10.0


@pytest.mark.parametrize("webhook", ["", "   ", "http://insecure.example", "not a url"])
def test_an_unusable_webhook_is_a_config_error(webhook: str) -> None:
    with pytest.raises(AlertConfigError):
        send_slack({"text": "hi"}, webhook, transport=FakeTransport())


def test_an_oversized_body_is_refused_before_it_is_sent() -> None:
    transport = FakeTransport()
    oversized = {"text": "x" * (MAX_BODY_BYTES + 1)}

    with pytest.raises(AlertConfigError):
        send_slack(oversized, PLACEHOLDER_WEBHOOK, transport=transport)
    assert transport.calls == []


def test_a_transport_failure_becomes_a_typed_error_without_the_url() -> None:
    def explode(*, url: str, body: bytes, timeout: float) -> tuple[int, str]:
        raise OSError(f"connection refused to {url}")

    with pytest.raises(AlertTransportError) as caught:
        send_slack({"text": "hi"}, PLACEHOLDER_WEBHOOK, transport=explode)

    assert PLACEHOLDER_WEBHOOK not in str(caught.value)


def test_a_non_success_status_becomes_a_typed_error_without_the_url() -> None:
    transport = FakeTransport(status=403, body="invalid_token")

    with pytest.raises(AlertResponseError) as caught:
        send_slack({"text": "hi"}, PLACEHOLDER_WEBHOOK, transport=transport)

    assert "403" in str(caught.value)
    assert PLACEHOLDER_WEBHOOK not in str(caught.value)


def test_a_successful_send_returns_the_response_body() -> None:
    assert send_slack({"text": "hi"}, PLACEHOLDER_WEBHOOK, transport=FakeTransport()) == "ok"


# --------------------------------------------------------------------------
# the CLI gate
# --------------------------------------------------------------------------


def run_with(tmp_path: Path, *args: str, verdict: str = "REGRESSION") -> Path:
    return build_run_dir(tmp_path, verdict=verdict)


def test_the_default_is_a_dry_run_that_prints_the_payload_and_sends_nothing(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport()
    monkeypatch.setenv(WEBHOOK_ENV_VAR, PLACEHOLDER_WEBHOOK)
    monkeypatch.setattr(alert_run, "default_transport", lambda: transport)
    run_dir = run_with(tmp_path)

    code = alert_run.main(["--run", str(run_dir)])
    printed = capsys.readouterr().out

    assert code == 0
    assert transport.calls == []
    assert json.loads(printed[printed.index("{") :])["blocks"]
    assert "DRY RUN" in printed


def test_send_posts_when_the_verdict_is_a_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport()
    monkeypatch.setenv(WEBHOOK_ENV_VAR, PLACEHOLDER_WEBHOOK)
    monkeypatch.setattr(alert_run, "default_transport", lambda: transport)

    code = alert_run.main(["--run", str(run_with(tmp_path)), "--send"])

    assert code == 0
    assert len(transport.calls) == 1


def test_send_stays_quiet_for_a_clean_run_unless_always_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    transport = FakeTransport()
    monkeypatch.setenv(WEBHOOK_ENV_VAR, PLACEHOLDER_WEBHOOK)
    monkeypatch.setattr(alert_run, "default_transport", lambda: transport)
    run_dir = run_with(tmp_path, verdict="NO_REGRESSION")

    assert alert_run.main(["--run", str(run_dir), "--send"]) == 0
    assert transport.calls == []
    assert "NO_REGRESSION" in capsys.readouterr().out

    assert alert_run.main(["--run", str(run_dir), "--send", "--always"]) == 0
    assert len(transport.calls) == 1


def test_send_without_the_secret_exits_two_with_an_actionable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.delenv(WEBHOOK_ENV_VAR, raising=False)

    code = alert_run.main(["--run", str(run_with(tmp_path)), "--send"])

    assert code == 2
    assert WEBHOOK_ENV_VAR in capsys.readouterr().err


def test_a_dry_run_needs_no_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(WEBHOOK_ENV_VAR, raising=False)

    assert alert_run.main(["--run", str(run_with(tmp_path))]) == 0


def test_a_failed_send_exits_one_without_leaking_the_webhook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv(WEBHOOK_ENV_VAR, PLACEHOLDER_WEBHOOK)
    monkeypatch.setattr(alert_run, "default_transport", lambda: FakeTransport(status=500))

    code = alert_run.main(["--run", str(run_with(tmp_path)), "--send"])

    assert code == 1
    assert PLACEHOLDER_WEBHOOK not in capsys.readouterr().err


def test_a_run_that_cannot_be_read_exits_three(tmp_path: Path, capsys) -> None:
    assert alert_run.main(["--run", str(tmp_path / "absent")]) == 3
    assert "ReportInputError" in capsys.readouterr().err


def test_a_report_url_is_passed_into_the_payload(tmp_path: Path, capsys) -> None:
    alert_run.main(["--run", str(run_with(tmp_path)), "--report-url", "https://example.com/1"])

    assert "https://example.com/1" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the default transport
# --------------------------------------------------------------------------


class FakeResponse:
    """The context-manager shape `urlopen` returns."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, size: int) -> bytes:
        return self._body[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def test_the_default_transport_posts_json_and_returns_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    seen: dict = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["type"] = request.get_header("Content-type")
        seen["timeout"] = timeout
        return FakeResponse(200, b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    status, body = slack.default_transport(
        url=PLACEHOLDER_WEBHOOK, body=b'{"text":"hi"}', timeout=10.0
    )

    assert (status, body) == (200, "ok")
    assert seen["method"] == "POST"
    assert seen["type"] == "application/json"
    assert seen["timeout"] == 10.0


def test_the_default_transport_reports_an_http_error_as_a_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error
    import urllib.request

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, io.BytesIO(b"no_service")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert slack.default_transport(url=PLACEHOLDER_WEBHOOK, body=b"{}", timeout=1.0) == (
        404,
        "no_service",
    )


def test_the_default_transport_turns_a_network_failure_into_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error
    import urllib.request

    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(AlertTransportError) as caught:
        slack.default_transport(url=PLACEHOLDER_WEBHOOK, body=b"{}", timeout=1.0)

    assert PLACEHOLDER_WEBHOOK not in str(caught.value)


def test_a_transport_that_raises_a_value_error_becomes_a_transport_error() -> None:
    def explode(*, url: str, body: bytes, timeout: float) -> tuple[int, str]:
        raise ValueError("unknown url type")

    with pytest.raises(AlertTransportError):
        send_slack({"text": "hi"}, PLACEHOLDER_WEBHOOK, transport=explode)


def test_the_cli_uses_the_real_transport_by_default() -> None:
    assert alert_run.default_transport() is slack.default_transport
