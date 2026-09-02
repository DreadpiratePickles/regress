"""Tests for the Gemini adapter.

No test in this file touches the network or reads a real API key. The vendor
client is replaced with an in-memory fake before every construction, `time.sleep`
is replaced with a recorder, and the SDK error objects are built by hand so the
classification logic is exercised against the same attributes the real SDK sets.
"""

import httpx
import pytest
from google.genai import errors as genai_errors

from regression_detect.providers import gemini
from regression_detect.providers.base import (
    ProviderConfigError,
    ProviderResponseError,
    ProviderTransientError,
)
from regression_detect.target.config import (
    DEFAULT_TARGET_MODEL_ID,
    TARGET_MODEL_ID_ENV_VAR,
    target_model_id,
)

MODEL_ID = "test-model"
FAKE_KEY = "not-a-real-key"


# --- fakes ------------------------------------------------------------------


def api_error(code: int, status: str = "ERROR") -> genai_errors.APIError:
    """A real SDK APIError with the attributes `_classify_api_error` inspects."""
    return genai_errors.APIError(code, {"error": {"status": status, "message": "boom"}})


class FakeResponse:
    """Stands in for the SDK response object: only `.text` is read."""

    def __init__(self, text: object, prompt_feedback: object = None) -> None:
        self.text = text
        self.prompt_feedback = prompt_feedback


class FakeModels:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    def generate_content(self, *, model: str, contents: str, config: object) -> object:
        self.calls.append({"model": model, "contents": contents, "config": config})
        index = min(len(self.calls) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.models = FakeModels(outcomes)


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace `time.sleep` with a no-op that records the requested durations."""
    recorded: list[float] = []
    monkeypatch.setattr(gemini.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def make_provider(monkeypatch: pytest.MonkeyPatch):
    """Build a `GeminiProvider` wired to an in-memory client, never the network."""

    def _make(outcomes: list[object]) -> tuple[gemini.GeminiProvider, FakeClient]:
        client = FakeClient(outcomes)
        monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: client)
        provider = gemini.GeminiProvider(model_id=MODEL_ID, api_key=FAKE_KEY)
        return provider, client

    return _make


def complete(provider: gemini.GeminiProvider) -> str:
    return provider.complete(system="rules", user="ticket", temperature=0.2)


# --- construction -----------------------------------------------------------


def test_construction_passes_the_configured_timeout_to_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def spy(**kwargs: object) -> FakeClient:
        seen.update(kwargs)
        return FakeClient([FakeResponse("ok")])

    monkeypatch.setattr(gemini.genai, "Client", spy)
    provider = gemini.GeminiProvider(model_id=MODEL_ID, api_key=FAKE_KEY)

    assert provider.model_id == MODEL_ID
    assert seen["api_key"] == FAKE_KEY
    assert seen["http_options"].timeout == gemini.REQUEST_TIMEOUT_MS


@pytest.mark.parametrize("model_id", ["", "   ", None])
def test_a_blank_model_id_is_a_config_error(model_id: object) -> None:
    with pytest.raises(ProviderConfigError, match="model_id"):
        gemini.GeminiProvider(model_id=model_id, api_key=FAKE_KEY)


@pytest.mark.parametrize("api_key", ["", "   ", None])
def test_a_blank_api_key_is_a_config_error_naming_the_env_var(api_key: object) -> None:
    with pytest.raises(ProviderConfigError) as caught:
        gemini.GeminiProvider(model_id=MODEL_ID, api_key=api_key)

    assert gemini.API_KEY_ENV_VAR in str(caught.value)


def test_a_client_that_refuses_to_build_becomes_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**kwargs: object) -> FakeClient:
        raise RuntimeError("bad config")

    monkeypatch.setattr(gemini.genai, "Client", boom)

    with pytest.raises(ProviderConfigError) as caught:
        gemini.GeminiProvider(model_id=MODEL_ID, api_key=FAKE_KEY)

    assert FAKE_KEY not in str(caught.value)


# --- retries on transient failures ------------------------------------------


@pytest.mark.parametrize("code", [429, 503, 500, 408, 409, 502, 504])
def test_a_transient_status_is_retried_to_the_attempt_limit_then_raised(
    make_provider, sleeps: list[float], code: int
) -> None:
    provider, client = make_provider([api_error(code, "RESOURCE_EXHAUSTED")])

    with pytest.raises(ProviderTransientError) as caught:
        complete(provider)

    assert len(client.models.calls) == gemini.MAX_ATTEMPTS
    assert len(sleeps) == gemini.MAX_ATTEMPTS - 1
    assert str(gemini.MAX_ATTEMPTS) in str(caught.value)


def test_a_transient_failure_followed_by_success_returns_on_the_second_attempt(
    make_provider, sleeps: list[float]
) -> None:
    provider, client = make_provider(
        [api_error(429, "RESOURCE_EXHAUSTED"), FakeResponse("A short summary.")]
    )

    assert complete(provider) == "A short summary."
    assert len(client.models.calls) == 2
    assert len(sleeps) == 1


def test_a_first_attempt_success_never_sleeps(make_provider, sleeps: list[float]) -> None:
    provider, client = make_provider([FakeResponse("A short summary.")])

    assert complete(provider) == "A short summary."
    assert len(client.models.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "exc",
    [
        httpx.TimeoutException("timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection refused"),
    ],
)
def test_a_network_failure_is_treated_as_transient(
    make_provider, sleeps: list[float], exc: Exception
) -> None:
    provider, client = make_provider([exc])

    with pytest.raises(ProviderTransientError) as caught:
        complete(provider)

    assert len(client.models.calls) == gemini.MAX_ATTEMPTS
    assert type(exc).__name__ in str(caught.value)


def test_a_network_failure_that_clears_returns_the_reply(
    make_provider, sleeps: list[float]
) -> None:
    provider, client = make_provider(
        [httpx.TimeoutException("timed out"), FakeResponse("A short summary.")]
    )

    assert complete(provider) == "A short summary."
    assert len(client.models.calls) == 2


# --- failures that are never retried ----------------------------------------


@pytest.mark.parametrize("code", [401, 403])
def test_a_credential_rejection_is_a_config_error_after_one_attempt(
    make_provider, sleeps: list[float], code: int
) -> None:
    provider, client = make_provider([api_error(code, "PERMISSION_DENIED")])

    with pytest.raises(ProviderConfigError) as caught:
        complete(provider)

    assert len(client.models.calls) == 1
    assert sleeps == []
    assert gemini.API_KEY_ENV_VAR in str(caught.value)
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("code", [400, 404, 422])
def test_a_non_retryable_api_error_is_a_response_error_after_one_attempt(
    make_provider, sleeps: list[float], code: int
) -> None:
    provider, client = make_provider([api_error(code, "INVALID_ARGUMENT")])

    with pytest.raises(ProviderResponseError) as caught:
        complete(provider)

    assert len(client.models.calls) == 1
    assert sleeps == []
    assert str(code) in str(caught.value)


# --- response validation ----------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_an_empty_or_whitespace_reply_is_a_response_error(
    make_provider, sleeps: list[float], text: str
) -> None:
    provider, client = make_provider([FakeResponse(text)])

    with pytest.raises(ProviderResponseError, match="empty response"):
        complete(provider)

    assert len(client.models.calls) == 1
    assert sleeps == []


def test_a_missing_reply_reports_the_prompt_feedback(make_provider) -> None:
    provider, _ = make_provider([FakeResponse(None, prompt_feedback="SAFETY")])

    with pytest.raises(ProviderResponseError, match="SAFETY"):
        complete(provider)


def test_a_non_string_reply_is_a_response_error(make_provider) -> None:
    provider, _ = make_provider([FakeResponse(42)])

    with pytest.raises(ProviderResponseError):
        complete(provider)


def test_the_reply_is_returned_verbatim_and_the_request_carries_the_arguments(
    make_provider,
) -> None:
    provider, client = make_provider([FakeResponse("  padded summary  ")])

    assert provider.complete(system="rules", user="ticket", temperature=0.7) == (
        "  padded summary  "
    )
    call = client.models.calls[0]
    assert call["model"] == MODEL_ID
    assert call["contents"] == "ticket"
    assert call["config"].system_instruction == "rules"
    assert call["config"].temperature == 0.7


# --- backoff ----------------------------------------------------------------


def test_backoff_durations_grow_between_attempts(
    make_provider, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Full jitter draws from [0, ceiling); pinning the draw to the ceiling makes
    # the growth of the ceiling itself observable.
    monkeypatch.setattr(gemini.random, "uniform", lambda low, high: high)
    provider, _ = make_provider([api_error(429, "RESOURCE_EXHAUSTED")])

    with pytest.raises(ProviderTransientError):
        complete(provider)

    assert sleeps == [
        gemini.BACKOFF_BASE_SECONDS * (2**attempt) for attempt in range(gemini.MAX_ATTEMPTS - 1)
    ]
    assert sleeps == sorted(sleeps)
    assert sleeps[0] < sleeps[-1]


def test_backoff_is_capped_and_never_negative() -> None:
    ceilings = [gemini._sleep_seconds(attempt) for attempt in range(20)]

    assert all(0.0 <= value <= gemini.BACKOFF_MAX_SECONDS for value in ceilings)


def test_the_backoff_ceiling_saturates_at_the_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini.random, "uniform", lambda low, high: high)

    assert gemini._sleep_seconds(0) == gemini.BACKOFF_BASE_SECONDS
    assert gemini._sleep_seconds(1) == gemini.BACKOFF_BASE_SECONDS * 2
    assert gemini._sleep_seconds(50) == gemini.BACKOFF_MAX_SECONDS


# --- building from the environment ------------------------------------------


@pytest.fixture
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop `load_dotenv` from reading a real .env file on the developer's disk."""
    monkeypatch.setattr(gemini, "load_dotenv", lambda *args, **kwargs: False)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_key_in_the_environment_is_an_actionable_config_error(
    monkeypatch: pytest.MonkeyPatch, no_dotenv: None, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(gemini.API_KEY_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(gemini.API_KEY_ENV_VAR, value)

    with pytest.raises(ProviderConfigError) as caught:
        gemini.gemini_provider_from_env(MODEL_ID)

    message = str(caught.value)
    assert gemini.API_KEY_ENV_VAR in message
    assert ".env" in message


def test_a_key_read_from_the_environment_never_reaches_an_error_message(
    monkeypatch: pytest.MonkeyPatch, no_dotenv: None
) -> None:
    secret = "sk-do-not-leak-this-value"
    monkeypatch.setenv(gemini.API_KEY_ENV_VAR, secret)

    def boom(**kwargs: object) -> FakeClient:
        raise RuntimeError(f"upstream detail mentioning {secret}")

    monkeypatch.setattr(gemini.genai, "Client", boom)

    with pytest.raises(ProviderConfigError) as caught:
        gemini.gemini_provider_from_env(MODEL_ID)

    assert secret not in str(caught.value)


def test_a_present_key_builds_a_working_provider(
    monkeypatch: pytest.MonkeyPatch, no_dotenv: None
) -> None:
    monkeypatch.setenv(gemini.API_KEY_ENV_VAR, FAKE_KEY)
    client = FakeClient([FakeResponse("A short summary.")])
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: client)

    provider = gemini.gemini_provider_from_env(MODEL_ID)

    assert isinstance(provider, gemini.GeminiProvider)
    assert provider.model_id == MODEL_ID
    assert complete(provider) == "A short summary."


# --- target model configuration ---------------------------------------------


def test_target_model_id_defaults_when_the_override_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TARGET_MODEL_ID_ENV_VAR, raising=False)

    assert target_model_id() == DEFAULT_TARGET_MODEL_ID


def test_target_model_id_honours_the_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TARGET_MODEL_ID_ENV_VAR, "some-other-model")

    assert target_model_id() == "some-other-model"


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_override_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(TARGET_MODEL_ID_ENV_VAR, value)

    assert target_model_id() == DEFAULT_TARGET_MODEL_ID


def test_the_override_is_stripped_of_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TARGET_MODEL_ID_ENV_VAR, "  spaced-model  ")

    assert target_model_id() == "spaced-model"
