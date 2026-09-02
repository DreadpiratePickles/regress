"""Tests for `load_target`: a `[target]` section becomes a Target, or a typed error.

Split out of `test_target_adapters.py` only to keep both files under the
400-line limit; the factory is the same seam.

The factory is a boundary — the section comes from a file a human edited — so
every rejection names the key or the kind that is wrong, and no kind is built
before its keys are checked.
"""

import sys
from pathlib import Path

import pytest

from regression_detect.providers.fake import FakeProvider
from regression_detect.target.adapters.base import TargetConfigError
from regression_detect.target.adapters.builtin import BuiltinSummarizerTarget
from regression_detect.target.adapters.command import CommandTarget
from regression_detect.target.adapters.factory import load_target
from regression_detect.target.adapters.http import HttpTarget

FIXTURE_APP = Path(__file__).resolve().parent / "fixtures" / "fake_target_app.py"


def provider_factory() -> FakeProvider:
    provider_factory.calls += 1
    return FakeProvider("A short summary.")


provider_factory.calls = 0


@pytest.fixture(autouse=True)
def _reset_factory_calls():
    provider_factory.calls = 0


def build(config):
    return load_target(config, provider_factory=provider_factory)


# --- builtin ----------------------------------------------------------------


def test_the_builtin_kind_builds_the_packaged_summarizer() -> None:
    target = build({"kind": "builtin"})

    assert isinstance(target, BuiltinSummarizerTarget)
    assert provider_factory.calls == 1
    assert target.provenance()["model_id"] == "fake-provider"


def test_the_builtin_kind_accepts_a_prompt_path_and_a_temperature(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Summarize the ticket.\n", encoding="utf-8")

    provenance = build(
        {"kind": "builtin", "prompt_path": str(prompt), "temperature": 0.7}
    ).provenance()

    assert provenance["prompt_path"].endswith("prompt.md")
    assert provenance["temperature"] == "0.7"


@pytest.mark.parametrize("temperature", ["hot", True, [0.2]])
def test_a_builtin_temperature_that_is_not_a_number_is_refused(temperature) -> None:
    with pytest.raises(TargetConfigError, match="temperature"):
        build({"kind": "builtin", "temperature": temperature})


# --- command ----------------------------------------------------------------


def test_the_command_kind_builds_a_command_target() -> None:
    target = build({"kind": "command", "argv": [sys.executable, str(FIXTURE_APP)]})

    assert isinstance(target, CommandTarget)
    assert provider_factory.calls == 0, "an external target must not need a model provider"


def test_the_command_kind_carries_its_options_through(tmp_path: Path) -> None:
    provenance = build(
        {
            "kind": "command",
            "argv": [sys.executable, str(FIXTURE_APP)],
            "timeout_s": 12.5,
            "cwd": str(tmp_path),
            "env_allowlist": ["GEMINI_API_KEY"],
        }
    ).provenance()

    assert provenance["timeout_s"] == "12.5"
    assert provenance["cwd"].endswith(tmp_path.name)
    assert provenance["env_allowlist"] == "GEMINI_API_KEY"


def test_a_command_without_argv_is_refused() -> None:
    with pytest.raises(TargetConfigError, match="argv"):
        build({"kind": "command"})


def test_a_command_argv_given_as_a_shell_string_is_refused() -> None:
    """argv is a list so nothing is ever handed to a shell to re-parse."""
    with pytest.raises(TargetConfigError):
        build({"kind": "command", "argv": "python app.py | tee log"})


# --- http -------------------------------------------------------------------


def test_the_http_kind_builds_an_http_target() -> None:
    target = build({"kind": "http", "url": "https://example.test/summarize"})

    assert isinstance(target, HttpTarget)
    assert provider_factory.calls == 0


def test_the_http_kind_carries_its_options_through() -> None:
    provenance = build(
        {
            "kind": "http",
            "url": "https://example.test/summarize",
            "timeout_s": 9,
            "input_field": "ticket",
            "output_field": "summary",
            "auth_header_env": "MY_APP_TOKEN",
        }
    ).provenance()

    assert provenance["input_field"] == "ticket"
    assert provenance["output_field"] == "summary"
    assert provenance["auth_header_env"] == "MY_APP_TOKEN"
    assert provenance["timeout_s"] == "9.0"


def test_an_http_target_without_a_url_is_refused() -> None:
    with pytest.raises(TargetConfigError, match="url"):
        build({"kind": "http"})


# --- the shape of the section itself ----------------------------------------


def test_an_unknown_kind_names_the_kinds_that_exist() -> None:
    with pytest.raises(TargetConfigError, match="grpc") as caught:
        build({"kind": "grpc", "url": "x"})

    for known in ("builtin", "command", "http"):
        assert known in str(caught.value)


@pytest.mark.parametrize("kind", [None, "", "   ", 7])
def test_a_missing_or_unusable_kind_is_refused(kind) -> None:
    config = {} if kind is None else {"kind": kind}
    with pytest.raises(TargetConfigError, match="kind"):
        build(config)


@pytest.mark.parametrize(
    "config",
    [
        {"kind": "builtin", "url": "https://example.test/x"},
        {"kind": "command", "argv": ["true"], "input_field": "ticket"},
        {"kind": "http", "url": "https://example.test/x", "argv": ["true"]},
        {"kind": "builtin", "tempreature": 0.2},
    ],
    ids=["builtin-http-key", "command-http-key", "http-command-key", "typo"],
)
def test_a_key_that_does_not_belong_to_the_kind_is_refused(config) -> None:
    with pytest.raises(TargetConfigError):
        build(config)


@pytest.mark.parametrize("config", ["kind = builtin", ["kind"], None, 7])
def test_a_section_that_is_not_a_table_is_refused(config) -> None:
    with pytest.raises(TargetConfigError):
        build(config)
