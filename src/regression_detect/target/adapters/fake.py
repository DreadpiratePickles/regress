"""The canned target a `--dry-run` measures. It calls nothing, ever.

A dry run exists so the pipeline can be exercised with no API key, no network
and no cost — which has to hold whatever the `[target]` section names. Building
a `CommandTarget` under `--dry-run` would spawn somebody's app, and building an
`HttpTarget` would POST to somebody's endpoint: a "dry" run that runs the thing.

So for any non-builtin kind the runner substitutes this target instead. Its
identity says so out loud rather than impersonating the configured feature: a
run produced here records `target_id = "fake:dry-run"`, so a dry run's numbers
can never be mistaken for a measurement of the real target, or pooled into a
baseline that claims to describe it.
"""

from .base import validate_input_text

FAKE_TARGET_ID = "fake:dry-run"
"""Stable identity of a dry run. Deliberately not a real target's id."""

FAKE_MODEL_ID = "dry-run-fake"
"""Matches the fake provider's model id, so both dry-run paths read alike."""


class FakeTarget:
    """Return one canned answer to every input. No process, no request, no model.

    Args:
        output: The canned answer. Held as-is; the same text for every case.
        model_id: What the manifest records as the model behind this run.
    """

    def __init__(self, output: str, *, model_id: str = FAKE_MODEL_ID) -> None:
        self.target_id = FAKE_TARGET_ID
        self._output = output
        self._model_id = model_id

    def run(self, input_text: str) -> str:
        """Return the canned answer.

        The input is still validated at the boundary, so a dry run exercises the
        same input checks a real run would.

        Raises:
            TargetExecutionError: if the input is not usable text.
        """
        validate_input_text(input_text, target_id=self.target_id)
        return self._output

    def provenance(self) -> dict[str, str]:
        """Identity that says "this was a dry run" and nothing that claims more."""
        return {
            "kind": "fake",
            "target_id": self.target_id,
            "model_id": self._model_id,
            "note": "dry run: no target was called and no model was called",
        }
