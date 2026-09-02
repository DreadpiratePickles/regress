"""Tests for the target adapters: builtin, command and http.

The point of the adapters is that the detector can regression-test somebody
else's feature, so these tests exercise the two external kinds the way a user
would: the command adapter against a real subprocess (`tests/fixtures/
fake_target_app.py`, run with `sys.executable`), and the http adapter against
`httpx.MockTransport`. No test calls the network and no test needs an API key.
"""

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from regression_detect.providers.fake import FakeProvider
from regression_detect.target.adapters.base import (
    Target,
    TargetConfigError,
    TargetExecutionError,
    TargetResponseError,
    TargetTimeoutError,
)
from regression_detect.target.adapters.builtin import BuiltinSummarizerTarget
from regression_detect.target.adapters.command import MAX_STDERR_CHARS, CommandTarget
from regression_detect.target.adapters.http import MAX_RESPONSE_BYTES, HttpTarget

FIXTURE_APP = Path(__file__).resolve().parent / "fixtures" / "fake_target_app.py"
APP_ARGV = [sys.executable, str(FIXTURE_APP)]
TICKET = "My order never arrived and support has not replied."


def command_target(*extra: str, **kwargs) -> CommandTarget:
    return CommandTarget([*APP_ARGV, *extra], **kwargs)


def http_target(handler, **kwargs) -> HttpTarget:
    return HttpTarget(
        "https://example.test/summarize",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def json_handler(payload, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        handler.requests.append(request)
        return httpx.Response(status, json=payload)

    handler.requests = []
    return handler


# --- the protocol -----------------------------------------------------------


def test_all_three_kinds_satisfy_the_target_protocol() -> None:
    targets = [
        BuiltinSummarizerTarget(FakeProvider("A summary.")),
        command_target(),
        http_target(json_handler({"output": "A summary."})),
    ]

    for target in targets:
        assert isinstance(target, Target)
        assert isinstance(target.target_id, str) and target.target_id
        provenance = target.provenance()
        assert all(isinstance(key, str) for key in provenance)
        assert all(isinstance(value, str) for value in provenance.values())


# --- the builtin summarizer -------------------------------------------------


def test_the_builtin_target_summarizes_through_the_provider() -> None:
    provider = FakeProvider("A short summary.")

    output = BuiltinSummarizerTarget(provider).run(TICKET)

    assert output == "A short summary."
    assert TICKET in provider.calls[0]["user"]


def test_builtin_provenance_pins_the_prompt_hash_and_the_model() -> None:
    provenance = BuiltinSummarizerTarget(FakeProvider("x"), temperature=0.4).provenance()

    assert provenance["kind"] == "builtin"
    assert provenance["model_id"] == "fake-provider"
    assert provenance["provider_class"] == "FakeProvider"
    assert len(provenance["prompt_sha256"]) == 64
    assert provenance["prompt_path"].endswith("summarize_v1.md")
    assert provenance["temperature"] == "0.4"


def test_two_builtin_targets_on_the_same_prompt_share_an_identity() -> None:
    first = BuiltinSummarizerTarget(FakeProvider("x"))
    second = BuiltinSummarizerTarget(FakeProvider("y"))

    assert first.target_id == second.target_id


# --- the command target -----------------------------------------------------


def test_a_command_target_runs_the_app_and_returns_its_stdout() -> None:
    assert command_target().run(TICKET) == TICKET.upper()


def test_a_non_zero_exit_names_the_code_and_keeps_only_the_stderr_tail() -> None:
    with pytest.raises(TargetExecutionError) as caught:
        command_target("--fail").run(TICKET)

    message = str(caught.value)
    assert "3" in message
    assert "TAIL-MARKER" in message
    assert "HEAD-MARKER" not in message
    assert TICKET not in message


def test_a_command_that_stalls_raises_a_timeout() -> None:
    with pytest.raises(TargetTimeoutError):
        command_target("--sleep", "5", timeout_s=0.3).run(TICKET)


def test_a_command_that_prints_nothing_is_a_response_error() -> None:
    with pytest.raises(TargetResponseError):
        command_target("--silent").run(TICKET)


def test_a_command_that_cannot_be_started_is_an_execution_error(tmp_path: Path) -> None:
    with pytest.raises(TargetExecutionError):
        CommandTarget([str(tmp_path / "no-such-binary")]).run(TICKET)


def test_only_allowlisted_variables_reach_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET_KEEP_ME", "kept")
    monkeypatch.setenv("TARGET_DROP_ME", "dropped")

    names = command_target("--print-env", env_allowlist=["TARGET_KEEP_ME"]).run(TICKET).split(",")

    assert "TARGET_KEEP_ME" in names
    assert "TARGET_DROP_ME" not in names
    assert "PATH" in names


def test_an_allowlisted_variable_that_is_unset_is_simply_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TARGET_ABSENT", raising=False)

    names = command_target("--print-env", env_allowlist=["TARGET_ABSENT"]).run(TICKET).split(",")

    assert "TARGET_ABSENT" not in names


@pytest.mark.parametrize(
    "argv",
    [[], "python script.py", [1, 2], [""], ["python", 3], ("nested", ["list"])],
    ids=["empty", "shell-string", "non-strings", "blank", "mixed", "nested"],
)
def test_argv_must_be_a_non_empty_list_of_strings(argv) -> None:
    with pytest.raises(TargetConfigError):
        CommandTarget(argv)


@pytest.mark.parametrize("timeout", [0, -1, "5", None, True])
def test_a_command_timeout_must_be_a_positive_number(timeout) -> None:
    with pytest.raises(TargetConfigError):
        command_target(timeout_s=timeout)


def test_a_working_directory_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TargetConfigError):
        command_target(cwd=tmp_path / "absent")


def test_an_env_allowlist_must_hold_variable_names() -> None:
    with pytest.raises(TargetConfigError):
        command_target(env_allowlist=["GOOD", 7])


@pytest.mark.parametrize("bad_input", ["", "   ", None, 7])
def test_the_command_target_validates_its_input(bad_input) -> None:
    with pytest.raises(TargetExecutionError):
        command_target().run(bad_input)


def test_command_provenance_records_the_argv_and_a_stable_identity() -> None:
    provenance = command_target(env_allowlist=["A", "B"]).provenance()

    assert provenance["kind"] == "command"
    assert json.loads(provenance["argv"]) == APP_ARGV
    assert len(provenance["argv_sha256"]) == 64
    assert provenance["env_allowlist"] == "A,B"
    assert provenance["target_id"] == command_target().target_id


def test_a_different_argv_is_a_different_target() -> None:
    assert command_target().target_id != command_target("--silent").target_id


def test_stderr_longer_than_the_bound_is_truncated() -> None:
    with pytest.raises(TargetExecutionError) as caught:
        command_target("--fail").run(TICKET)

    assert len(str(caught.value)) < MAX_STDERR_CHARS + 200


# --- the http target --------------------------------------------------------


def test_an_http_target_posts_the_input_and_reads_the_output() -> None:
    handler = json_handler({"output": "A short summary."})

    assert http_target(handler).run(TICKET) == "A short summary."

    request = handler.requests[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {"input": TICKET}


def test_the_field_names_are_configurable() -> None:
    handler = json_handler({"summary": "Done."})

    output = http_target(handler, input_field="ticket", output_field="summary").run(TICKET)

    assert output == "Done."
    assert json.loads(handler.requests[0].content) == {"ticket": TICKET}


@pytest.mark.parametrize("status", [301, 400, 401, 404, 500, 503])
def test_a_non_2xx_response_is_an_execution_error(status: int) -> None:
    with pytest.raises(TargetExecutionError, match=str(status)):
        http_target(json_handler({"output": "ignored"}, status=status)).run(TICKET)


def test_a_body_that_is_not_json_is_a_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(TargetResponseError):
        http_target(handler).run(TICKET)


@pytest.mark.parametrize(
    "payload",
    [{}, {"other": "x"}, {"output": None}, {"output": 7}, {"output": "   "}, ["output"]],
    ids=["empty", "wrong-key", "null", "number", "blank", "not-an-object"],
)
def test_a_body_without_a_usable_output_is_a_response_error(payload) -> None:
    with pytest.raises(TargetResponseError):
        http_target(json_handler(payload)).run(TICKET)


def test_a_response_larger_than_the_bound_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": "y" * (MAX_RESPONSE_BYTES + 1024)})

    with pytest.raises(TargetResponseError, match="large"):
        http_target(handler).run(TICKET)


def test_a_request_timeout_is_a_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TargetTimeoutError):
        http_target(handler).run(TICKET)


def test_a_transport_failure_is_an_execution_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(TargetExecutionError):
        http_target(handler).run(TICKET)


def test_no_authorization_header_is_sent_when_none_is_configured() -> None:
    handler = json_handler({"output": "ok"})

    http_target(handler).run(TICKET)

    assert "authorization" not in handler.requests[0].headers


def test_the_bearer_token_is_read_from_the_named_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TARGET_TOKEN", "s3cret-value")
    handler = json_handler({"output": "ok"})

    http_target(handler, auth_header_env="TARGET_TOKEN").run(TICKET)

    assert handler.requests[0].headers["authorization"] == "Bearer s3cret-value"


def test_a_configured_but_unset_token_fails_before_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TARGET_TOKEN", raising=False)
    handler = json_handler({"output": "ok"})

    with pytest.raises(TargetConfigError, match="TARGET_TOKEN") as caught:
        http_target(handler, auth_header_env="TARGET_TOKEN").run(TICKET)

    assert handler.requests == []
    assert "Bearer" not in str(caught.value)


def test_the_token_value_never_appears_in_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET_TOKEN", "s3cret-value")

    provenance = http_target(
        json_handler({"output": "ok"}), auth_header_env="TARGET_TOKEN"
    ).provenance()

    assert provenance["auth_header_env"] == "TARGET_TOKEN"
    assert "s3cret-value" not in json.dumps(provenance)


def test_http_provenance_records_the_url_and_the_fields() -> None:
    provenance = http_target(json_handler({"output": "ok"})).provenance()

    assert provenance["kind"] == "http"
    assert provenance["url"] == "https://example.test/summarize"
    assert provenance["input_field"] == "input"
    assert provenance["output_field"] == "output"
    assert provenance["target_id"].startswith("http:")


@pytest.mark.parametrize(
    "url", ["", "   ", "ftp://example.test/x", "example.test/x", 7, None]
)
def test_the_url_must_be_an_http_url(url) -> None:
    with pytest.raises(TargetConfigError):
        HttpTarget(url)


@pytest.mark.parametrize("timeout", [0, -1, "5", None, True])
def test_an_http_timeout_must_be_a_positive_number(timeout) -> None:
    with pytest.raises(TargetConfigError):
        HttpTarget("https://example.test/x", timeout_s=timeout)


@pytest.mark.parametrize("field", ["", "   ", 7, None])
def test_the_field_names_must_be_non_empty_strings(field) -> None:
    with pytest.raises(TargetConfigError):
        HttpTarget("https://example.test/x", input_field=field)
    with pytest.raises(TargetConfigError):
        HttpTarget("https://example.test/x", output_field=field)


@pytest.mark.parametrize("bad_input", ["", "   ", None, 7])
def test_the_http_target_validates_its_input(bad_input) -> None:
    with pytest.raises(TargetExecutionError):
        http_target(json_handler({"output": "ok"})).run(bad_input)


def test_the_environment_is_not_consulted_when_no_auth_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target with no auth must not depend on any variable being present."""
    monkeypatch.setattr(os, "environ", {})
    handler = json_handler({"output": "ok"})

    assert http_target(handler).run(TICKET) == "ok"
