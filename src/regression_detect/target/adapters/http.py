"""Call somebody else's feature over HTTP: POST some JSON, read some JSON back.

The adapter is deliberately unopinionated about the service's shape — the two
field names are configuration — because the point is to measure a feature that
already exists rather than to make its owner adopt a schema.

What is not configurable is the suspicion. The response is a boundary: the
status is checked before the body is read, the body is size-bounded before it is
parsed, the parse is guarded, and the output field must actually hold a non-empty
string. Any of those failing is a typed error, never an empty-string success.

A bearer token, when one is configured, is read from a named environment
variable at call time. The variable's *name* is recorded in provenance; its value
is never recorded, never logged, and never quoted into an error message.
"""

import os
from urllib.parse import urlparse

import httpx

from .base import (
    TargetConfigError,
    TargetExecutionError,
    TargetResponseError,
    TargetTimeoutError,
    tail,
    validate_input_text,
    validate_name,
    validate_timeout,
)

DEFAULT_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 1024 * 1024
"""One MiB. A summary is kilobytes; anything larger is a bug or an attack."""

MAX_ERROR_BODY_CHARS = 300
"""How much of a failing response is quoted back, counted from the end."""

ALLOWED_SCHEMES = ("http", "https")


def _validate_url(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        raise TargetConfigError(f"'url' must be a non-empty string, got {url!r}")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise TargetConfigError(
            f"'url' must be an http:// or https:// URL with a host, got {url!r}"
        )
    return url.strip()


class HttpTarget:
    """A feature under test that is reachable as a JSON HTTP endpoint.

    Args:
        url: The endpoint to POST to.
        timeout_s: Seconds to wait for a response.
        input_field: JSON key the input text is sent under.
        output_field: JSON key the answer is read from.
        auth_header_env: Name of the environment variable holding a bearer
            token, or `None` to send no `Authorization` header at all.
        transport: An `httpx` transport, for tests. Production leaves it `None`.

    Raises:
        TargetConfigError: if any argument is unusable.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        input_field: str = "input",
        output_field: str = "output",
        auth_header_env: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = _validate_url(url)
        self._timeout_s = validate_timeout(timeout_s)
        self._input_field = validate_name(input_field, what="input_field")
        self._output_field = validate_name(output_field, what="output_field")
        self._auth_header_env = (
            validate_name(auth_header_env, what="auth_header_env")
            if auth_header_env is not None
            else None
        )
        self._transport = transport

        parsed = urlparse(self._url)
        self.target_id = f"http:{parsed.netloc}{parsed.path}"

    def _headers(self) -> dict[str, str]:
        """Build the request headers, reading the token only if one is configured.

        Raises:
            TargetConfigError: if a token variable is named but unset. The
                message names the variable, never a value.
        """
        headers = {"Content-Type": "application/json"}
        if self._auth_header_env is None:
            return headers
        token = os.environ.get(self._auth_header_env, "").strip()
        if not token:
            raise TargetConfigError(
                f"{self.target_id} needs a token: set {self._auth_header_env} in the "
                "environment. Its value is a credential and belongs in .env, never in a "
                "config file."
            )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    def _validated_output(self, response: httpx.Response) -> str:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise TargetResponseError(
                f"{self.target_id} returned {len(response.content)} bytes, which is too "
                f"large; the bound is {MAX_RESPONSE_BYTES}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TargetResponseError(
                f"{self.target_id} did not return JSON: {tail(response.text, MAX_ERROR_BODY_CHARS)}"
            ) from exc
        if not isinstance(payload, dict):
            raise TargetResponseError(
                f"{self.target_id} returned a JSON {type(payload).__name__}, expected an object"
            )
        value = payload.get(self._output_field)
        if not isinstance(value, str) or not value.strip():
            raise TargetResponseError(
                f"{self.target_id} returned no usable '{self._output_field}': "
                f"expected a non-empty string, got {type(value).__name__}"
            )
        return value.strip()

    def run(self, input_text: str) -> str:
        """POST `input_text` and return the validated answer.

        Raises:
            TargetConfigError: a configured token variable is unset.
            TargetTimeoutError: the endpoint did not answer in time.
            TargetExecutionError: the request could not be made, or the endpoint
                answered with a status outside 2xx.
            TargetResponseError: the body is oversized, not JSON, or carries no
                usable output field.
        """
        text = validate_input_text(input_text, target_id=self.target_id)
        headers = self._headers()

        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                response = client.post(
                    self._url, json={self._input_field: text}, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise TargetTimeoutError(
                f"{self.target_id} gave no answer within {self._timeout_s} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise TargetExecutionError(
                f"{self.target_id} could not be reached: {type(exc).__name__}"
            ) from exc

        if not 200 <= response.status_code < 300:
            raise TargetExecutionError(
                f"{self.target_id} returned HTTP {response.status_code}: "
                f"{tail(response.text, MAX_ERROR_BODY_CHARS)}"
            )
        return self._validated_output(response)

    def provenance(self) -> dict[str, str]:
        """The endpoint, its field names, and the *name* of the token variable."""
        return {
            "kind": "http",
            "target_id": self.target_id,
            "url": self._url,
            "timeout_s": str(self._timeout_s),
            "input_field": self._input_field,
            "output_field": self._output_field,
            "auth_header_env": self._auth_header_env or "",
        }
