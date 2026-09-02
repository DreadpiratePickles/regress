"""Gemini adapter: the only module that imports a model vendor's SDK.

Responsibilities kept here and nowhere else:
  - reading the API key from the environment (never logged, never in an error);
  - bounded retries with exponential backoff and jitter, transient errors only;
  - a request timeout;
  - mapping SDK exceptions onto the typed errors in `base.py`.
"""

import os
import random
import time

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from .base import (
    ProviderConfigError,
    ProviderResponseError,
    ProviderTransientError,
)

API_KEY_ENV_VAR = "GEMINI_API_KEY"
REQUEST_TIMEOUT_MS = 60_000
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 20.0
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


def _sleep_seconds(attempt: int) -> float:
    """Exponential backoff with full jitter, capped."""
    ceiling = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS)
    return random.uniform(0.0, ceiling)


class GeminiProvider:
    """A narrow adapter around one Gemini text generation call."""

    def __init__(self, model_id: str, api_key: str) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProviderConfigError("model_id must be a non-empty string")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderConfigError(
                f"A Gemini API key is required. Set {API_KEY_ENV_VAR} in your .env file."
            )

        self.model_id = model_id
        try:
            self._client = genai.Client(
                api_key=api_key,
                http_options=genai_types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
            )
        except Exception as exc:  # the SDK raises bare exceptions on bad config
            raise ProviderConfigError(
                f"Could not build the Gemini client for model {self.model_id}"
            ) from exc

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
        )

        last_transient: ProviderTransientError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.models.generate_content(
                    model=self.model_id,
                    contents=user,
                    config=config,
                )
            except genai_errors.APIError as exc:
                error = self._classify_api_error(exc)
                if not isinstance(error, ProviderTransientError):
                    raise error from exc
                last_transient = error
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_transient = ProviderTransientError(
                    f"Network failure calling model {self.model_id}: {type(exc).__name__}"
                )
            else:
                return self._extract_text(response)

            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(_sleep_seconds(attempt))

        assert last_transient is not None  # only reachable after a transient failure
        raise ProviderTransientError(
            f"Model {self.model_id} still failing after {MAX_ATTEMPTS} attempts: "
            f"{last_transient}"
        ) from last_transient

    def _classify_api_error(self, exc: genai_errors.APIError) -> Exception:
        """Map an SDK API error onto a typed provider error.

        The API key is never interpolated into the message; only the status code
        and the SDK's own status string are surfaced.
        """
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        detail = f"model {self.model_id}, status {code} {status}"

        if code in RETRYABLE_STATUS_CODES:
            return ProviderTransientError(f"Transient provider failure ({detail})")
        if code in (401, 403):
            return ProviderConfigError(
                f"Gemini rejected the credentials ({detail}). "
                f"Check that {API_KEY_ENV_VAR} is set to a valid, enabled key."
            )
        return ProviderResponseError(f"Provider call failed ({detail})")

    def _extract_text(self, response: object) -> str:
        """Validate the SDK response before anything downstream reads it."""
        text = getattr(response, "text", None)
        if text is None:
            reason = getattr(response, "prompt_feedback", None)
            raise ProviderResponseError(
                f"Model {self.model_id} returned no text "
                f"(finish reason or safety block: {reason})"
            )
        if not isinstance(text, str) or not text.strip():
            raise ProviderResponseError(f"Model {self.model_id} returned an empty response")
        return text


def gemini_provider_from_env(model_id: str) -> GeminiProvider:
    """Build a `GeminiProvider`, reading the API key from the environment.

    Raises:
        ProviderConfigError: if the key is absent, with an actionable message
            that never contains the key itself.
    """
    load_dotenv()
    api_key = os.environ.get(API_KEY_ENV_VAR, "")
    if not api_key.strip():
        raise ProviderConfigError(
            f"{API_KEY_ENV_VAR} is not set. Create a .env file in the repository root "
            f"containing a line '{API_KEY_ENV_VAR}=<your key>', or export the variable "
            "in your shell. Keys are never committed."
        )
    return GeminiProvider(model_id=model_id, api_key=api_key)
