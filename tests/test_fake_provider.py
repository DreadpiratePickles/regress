"""Tests for the in-memory provider used by dry runs and by every other test."""

import pytest

from regression_detect.providers.base import Provider
from regression_detect.providers.fake import FakeProvider


def test_single_string_is_returned_for_every_call() -> None:
    provider = FakeProvider("canned summary")

    first = provider.complete(system="s", user="u", temperature=0.2)
    second = provider.complete(system="s", user="u", temperature=0.2)

    assert first == "canned summary"
    assert second == "canned summary"


def test_list_of_responses_is_returned_in_order_then_cycles() -> None:
    provider = FakeProvider(["one", "two"])

    outputs = [provider.complete(system="s", user="u", temperature=0.0) for _ in range(3)]

    assert outputs == ["one", "two", "one"]


def test_every_call_is_recorded_with_its_arguments() -> None:
    provider = FakeProvider("ok")

    provider.complete(system="system text", user="user text", temperature=0.7)

    assert provider.calls == [
        {"system": "system text", "user": "user text", "temperature": 0.7}
    ]


def test_exposes_a_model_id_for_run_manifests() -> None:
    provider = FakeProvider("ok")

    assert isinstance(provider.model_id, str)
    assert provider.model_id != ""


def test_custom_model_id_is_honoured() -> None:
    provider = FakeProvider("ok", model_id="fake-xyz")

    assert provider.model_id == "fake-xyz"


def test_empty_response_list_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        FakeProvider([])


def test_satisfies_the_provider_protocol() -> None:
    assert isinstance(FakeProvider("ok"), Provider)
