"""Tests for the criterion judge: message construction, parsing, configuration.

Every judge call goes through `FakeProvider`; no test touches the network.
The parsing tests carry the weight here: a judge verdict is model output, and
model output is untrusted input.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from regression_detect.judge.config import (
    DEFAULT_JUDGE_MODEL_ID,
    JUDGE_MODEL_ID_ENV_VAR,
    judge_model_id,
)
from regression_detect.judge.criterion import (
    DEFAULT_JUDGE_PROMPT_PATH,
    InvalidJudgeInputError,
    JudgeParseError,
    Verdict,
    build_judge_user_message,
    judge_criterion,
    judge_prompt_sha256,
    load_judge_prompt,
    parse_verdict,
)
from regression_detect.providers.base import ProviderTransientError
from regression_detect.providers.fake import FakeProvider
from regression_detect.target.config import DEFAULT_TARGET_MODEL_ID

TICKET = "My Calderwood filter arrived in the wrong size."
SUMMARY = "The customer received the wrong filter size and wants the right one sent."
CRITERION = "States that the customer received the wrong filter size."


def verdict_json(*, passed: bool, reason: str = "The summary says so.") -> str:
    return json.dumps({"reason": reason, "passed": passed})


# --- parse_verdict: accepted shapes -----------------------------------------


def test_parse_verdict_accepts_minimal_json() -> None:
    verdict = parse_verdict('{"reason": "It says so.", "passed": true}')

    assert verdict == Verdict(passed=True, reason="It says so.")


def test_parse_verdict_accepts_a_false_verdict() -> None:
    verdict = parse_verdict('{"reason": "The summary omits it.", "passed": false}')

    assert verdict.passed is False
    assert verdict.reason == "The summary omits it."


def test_parse_verdict_tolerates_surrounding_whitespace() -> None:
    verdict = parse_verdict('\n\n   {"reason": "Fine.", "passed": true}  \n ')

    assert verdict.passed is True


def test_parse_verdict_tolerates_a_json_fence() -> None:
    verdict = parse_verdict('```json\n{"reason": "Fine.", "passed": true}\n```')

    assert verdict.passed is True


def test_parse_verdict_tolerates_a_bare_fence() -> None:
    verdict = parse_verdict('```\n{"reason": "Fine.", "passed": false}\n```')

    assert verdict.passed is False


def test_parse_verdict_accepts_keys_in_either_order() -> None:
    verdict = parse_verdict('{"passed": true, "reason": "Order does not matter."}')

    assert verdict == Verdict(passed=True, reason="Order does not matter.")


def test_verdict_is_frozen() -> None:
    verdict = Verdict(passed=True, reason="Fine.")

    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.passed = False  # type: ignore[misc]


# --- parse_verdict: rejected shapes -----------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param('{"reason": "No verdict."}', id="missing_passed"),
        pytest.param('{"passed": true}', id="missing_reason"),
        pytest.param('{"reason": "Yes.", "passed": "true"}', id="passed_is_a_string"),
        pytest.param('{"reason": "Yes.", "passed": 1}', id="passed_is_an_int"),
        pytest.param('{"reason": "Yes.", "passed": null}', id="passed_is_null"),
        pytest.param('{"reason": 5, "passed": true}', id="reason_is_not_a_string"),
        pytest.param('{"reason": "  ", "passed": true}', id="reason_is_blank"),
        pytest.param(
            '{"reason": "Yes.", "passed": true, "score": 0.9}', id="extra_key"
        ),
        pytest.param("The summary satisfies the criterion.", id="prose"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace_only"),
        pytest.param('["passed"]', id="json_array"),
        pytest.param("true", id="bare_json_true"),
        pytest.param(
            'Verdict: {"reason": "Yes.", "passed": true}', id="prose_before_json"
        ),
    ],
)
def test_parse_verdict_rejects_anything_else(raw: str) -> None:
    with pytest.raises(JudgeParseError):
        parse_verdict(raw)


def test_parse_verdict_rejects_a_non_string() -> None:
    with pytest.raises(JudgeParseError):
        parse_verdict(None)  # type: ignore[arg-type]


def test_judge_parse_error_is_a_value_error() -> None:
    assert issubclass(JudgeParseError, ValueError)


# --- the user message -------------------------------------------------------


def test_user_message_wraps_all_three_fields_in_delimiters() -> None:
    message = build_judge_user_message(TICKET, SUMMARY, CRITERION)

    for tag in ("ticket", "summary", "criterion"):
        assert f"<{tag}>" in message
        assert f"</{tag}>" in message
    assert message.index("<ticket>") < message.index("<summary>") < message.index("<criterion>")


def test_user_message_places_each_field_inside_its_own_delimiters() -> None:
    message = build_judge_user_message(TICKET, SUMMARY, CRITERION)

    ticket_block = message.split("<ticket>")[1].split("</ticket>")[0]
    summary_block = message.split("<summary>")[1].split("</summary>")[0]
    criterion_block = message.split("<criterion>")[1].split("</criterion>")[0]

    assert TICKET in ticket_block
    assert SUMMARY in summary_block
    assert CRITERION in criterion_block


# --- judge_criterion --------------------------------------------------------


def test_judge_criterion_returns_the_parsed_verdict() -> None:
    provider = FakeProvider(verdict_json(passed=True, reason="It states it."))

    verdict = judge_criterion(
        ticket=TICKET, summary=SUMMARY, criterion=CRITERION, provider=provider
    )

    assert verdict == Verdict(passed=True, reason="It states it.")


def test_judge_criterion_sends_the_prompt_file_as_the_system_message() -> None:
    provider = FakeProvider(verdict_json(passed=True))

    judge_criterion(ticket=TICKET, summary=SUMMARY, criterion=CRITERION, provider=provider)

    assert provider.calls[0]["system"] == load_judge_prompt()


def test_judge_criterion_never_puts_the_data_in_the_system_prompt() -> None:
    provider = FakeProvider(verdict_json(passed=True))

    judge_criterion(ticket=TICKET, summary=SUMMARY, criterion=CRITERION, provider=provider)

    system = provider.calls[0]["system"]
    assert TICKET not in system
    assert SUMMARY not in system
    assert CRITERION not in system


def test_judge_criterion_puts_all_three_fields_in_the_user_message() -> None:
    provider = FakeProvider(verdict_json(passed=True))

    judge_criterion(ticket=TICKET, summary=SUMMARY, criterion=CRITERION, provider=provider)

    user = provider.calls[0]["user"]
    assert TICKET in user
    assert SUMMARY in user
    assert CRITERION in user
    assert "<criterion>" in user


def test_judge_criterion_defaults_to_temperature_zero() -> None:
    provider = FakeProvider(verdict_json(passed=True))

    judge_criterion(ticket=TICKET, summary=SUMMARY, criterion=CRITERION, provider=provider)

    assert provider.calls[0]["temperature"] == 0.0


def test_a_parse_failure_propagates_and_never_becomes_a_false_verdict() -> None:
    provider = FakeProvider("The summary is fine, I would say it passes.")

    with pytest.raises(JudgeParseError):
        judge_criterion(
            ticket=TICKET, summary=SUMMARY, criterion=CRITERION, provider=provider
        )


class RefusingProvider:
    """A provider that always fails, to prove provider errors are not swallowed."""

    model_id = "refusing"

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        raise ProviderTransientError("rate limited")


def test_a_provider_failure_propagates() -> None:
    with pytest.raises(ProviderTransientError):
        judge_criterion(
            ticket=TICKET,
            summary=SUMMARY,
            criterion=CRITERION,
            provider=RefusingProvider(),
        )


@pytest.mark.parametrize("field", ["ticket", "summary", "criterion"])
@pytest.mark.parametrize("value", ["", "   ", None, 7])
def test_judge_criterion_rejects_a_blank_or_non_string_field(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "ticket": TICKET,
        "summary": SUMMARY,
        "criterion": CRITERION,
        "provider": FakeProvider(verdict_json(passed=True)),
    }
    kwargs[field] = value

    with pytest.raises(InvalidJudgeInputError):
        judge_criterion(**kwargs)  # type: ignore[arg-type]


def test_a_missing_prompt_file_is_not_a_silent_fallback(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        judge_criterion(
            ticket=TICKET,
            summary=SUMMARY,
            criterion=CRITERION,
            provider=FakeProvider(verdict_json(passed=True)),
            prompt_path=tmp_path / "absent.md",
        )


# --- the prompt file itself -------------------------------------------------


def test_the_packaged_judge_prompt_exists_and_states_the_response_shape() -> None:
    prompt = load_judge_prompt()

    assert DEFAULT_JUDGE_PROMPT_PATH.is_file()
    assert '"passed"' in prompt
    assert '"reason"' in prompt
    assert len(judge_prompt_sha256()) == 64


# --- configuration ----------------------------------------------------------


def test_judge_model_defaults_to_the_target_model_for_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(JUDGE_MODEL_ID_ENV_VAR, raising=False)

    assert judge_model_id() == DEFAULT_JUDGE_MODEL_ID
    assert DEFAULT_JUDGE_MODEL_ID == DEFAULT_TARGET_MODEL_ID


def test_judge_model_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(JUDGE_MODEL_ID_ENV_VAR, "some-other-model")

    assert judge_model_id() == "some-other-model"


def test_a_blank_override_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JUDGE_MODEL_ID_ENV_VAR, "   ")

    assert judge_model_id() == DEFAULT_JUDGE_MODEL_ID


def test_the_config_docstring_records_the_self_preference_reason() -> None:
    from regression_detect.judge import config

    assert "self-preference" in config.__doc__.lower()


def test_parse_verdict_rejects_a_fence_in_another_language() -> None:
    with pytest.raises(JudgeParseError):
        parse_verdict('```python\n{"reason": "Fine.", "passed": true}\n```')


def test_parse_verdict_rejects_an_unterminated_fence() -> None:
    with pytest.raises(JudgeParseError):
        parse_verdict('```json\n{"reason": "Fine.", "passed": true}')
