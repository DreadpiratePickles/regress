"""The provider seam: the only place that knows a model is being called.

Nothing outside `providers/` names a vendor. Every call site depends on the
`Provider` protocol and handles the typed errors declared here, so swapping
Gemini for another vendor is a change confined to this package.
"""

from typing import Protocol, runtime_checkable


class ProviderError(Exception):
    """Base class for every failure originating in a model provider."""


class ProviderConfigError(ProviderError):
    """The provider cannot be built: missing API key, unusable settings.

    Never retried: no amount of waiting supplies a missing credential.
    """


class ProviderResponseError(ProviderError):
    """The provider replied, but the reply is unusable (empty or malformed).

    Model output is untrusted input; a reply that fails validation is an error,
    never an empty-string success.
    """


class ProviderTransientError(ProviderError):
    """A failure that may succeed on retry: rate limit, timeout, 5xx."""


@runtime_checkable
class Provider(Protocol):
    """A narrow adapter around one text-in/text-out model call.

    Implementations must raise only `ProviderError` subclasses, so callers never
    have to catch vendor SDK exceptions.
    """

    model_id: str
    """Identifier of the model actually called, recorded in run manifests."""

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        """Return the model's text reply to `user` under the `system` rules."""
        ...
