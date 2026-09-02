"""An in-memory provider: canned replies, no network, every call recorded.

Used by `--dry-run` and by the test suite so the runner and the target feature
can be exercised without an API key and without spending money.
"""

from typing import Any


class FakeProvider:
    """Return canned text and record every call for later assertions.

    Args:
        responses: A single string returned for every call, or a list of
            strings returned in order and then cycled from the start.
        model_id: Identifier reported to run manifests.
    """

    def __init__(self, responses: list[str] | str, *, model_id: str = "fake-provider") -> None:
        canned = [responses] if isinstance(responses, str) else list(responses)
        if not canned:
            raise ValueError("FakeProvider needs at least one canned response")
        if not all(isinstance(item, str) for item in canned):
            raise ValueError("FakeProvider responses must all be strings")

        self._responses = canned
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        index = len(self.calls) % len(self._responses)
        self.calls.append({"system": system, "user": user, "temperature": temperature})
        return self._responses[index]
