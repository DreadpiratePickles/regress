"""The built-in ticket summarizer, wrapped as a `Target`.

This is the target the repository dogfoods. It is deliberately the thinnest
adapter in the package: `run()` is `summarize()`, and the provider's errors are
allowed through unchanged so a rate limit is still recorded as a
`ProviderTransientError` rather than being flattened into a target error.

Its provenance carries the two identifiers a baseline was always pinned to — the
prompt hash and the model id — which is why moving stage 01 onto the target seam
did not change a single hash in `baselines/`.
"""

from pathlib import Path

from ...providers.base import Provider
from ..summarizer import DEFAULT_PROMPT_PATH, prompt_sha256, summarize

BUILTIN_TARGET_ID = "builtin_summarizer"
DEFAULT_TEMPERATURE = 0.2


class BuiltinSummarizerTarget:
    """Run one support ticket through the packaged summarizer.

    Args:
        provider: The model adapter to call.
        prompt_path: System prompt file; defaults to the packaged v1 prompt.
        temperature: Sampling temperature passed to the provider.
    """

    def __init__(
        self,
        provider: Provider,
        *,
        prompt_path: Path = DEFAULT_PROMPT_PATH,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self.target_id = BUILTIN_TARGET_ID
        self._provider = provider
        self._prompt_path = Path(prompt_path)
        self._temperature = float(temperature)

    def run(self, input_text: str) -> str:
        """Summarize one ticket.

        Raises:
            InvalidTicketError, TicketTooLongError, SummaryValidationError: the
                summarizer's own boundary checks.
            ProviderError: the provider call itself failed.
            FileNotFoundError: the prompt file is missing.
        """
        return summarize(
            input_text,
            self._provider,
            prompt_path=self._prompt_path,
            temperature=self._temperature,
        )

    def provenance(self) -> dict[str, str]:
        """The prompt hash, the model id, and the sampling temperature."""
        # Imported here rather than at module scope: `runner` builds this target,
        # so importing it eagerly would close an import cycle.
        from ...runner import display_path

        return {
            "kind": "builtin",
            "target_id": self.target_id,
            "model_id": self._provider.model_id,
            "provider_class": type(self._provider).__name__,
            "prompt_path": display_path(self._prompt_path),
            "prompt_sha256": prompt_sha256(self._prompt_path),
            "temperature": str(self._temperature),
        }
