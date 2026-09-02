"""The target seam: what stage 01 needs from the feature under test.

A regression detector that can only measure its own summarizer is a demo. This
protocol is the whole contract a feature has to satisfy to be measured: give the
runner some text, get some text back, and say what you are so a run's outputs can
be traced to the thing that produced them.

Provenance is not decoration. A baseline is a statement about one target; if the
target's identity changes — a different prompt, a different binary, a different
endpoint — the baseline no longer describes what is being measured, and the
recorded provenance is what makes that visible instead of silently comparing two
different features.

Errors are typed for the same reason the provider errors are: a caller catches
`TargetError` and records a failed sample, never a vendor SDK exception and never
an empty-string success.
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

MAX_INPUT_CHARS = 20_000
"""Upper bound on one input. Bounds the request size and the cost per call."""


class TargetError(Exception):
    """Base class for every failure originating in a target under test."""


class TargetConfigError(TargetError):
    """The target cannot be built or used as configured.

    Never retried: no amount of waiting fixes a missing binary name, an
    unparseable `[target]` section, or a credential variable that is not set.
    """


class TargetExecutionError(TargetError):
    """The target ran and failed: a non-zero exit, a transport failure, an HTTP error."""


class TargetTimeoutError(TargetError):
    """The target did not answer inside its configured timeout."""


class TargetResponseError(TargetError):
    """The target answered, but the answer is unusable (empty or malformed).

    A target's output is untrusted input; a reply that fails validation is an
    error, never an empty-string success.
    """


@runtime_checkable
class Target(Protocol):
    """One text-in/text-out feature the detector can run golden cases through."""

    target_id: str
    """Stable identifier for the feature. Two runs sharing it are comparable."""

    def run(self, input_text: str) -> str:
        """Return the feature's answer to `input_text`.

        Raises:
            TargetError: for every failure, so a caller never has to catch a
                vendor SDK exception or a `subprocess` error.
        """
        ...

    def provenance(self) -> dict[str, str]:
        """Stable identifiers hashed into a run manifest. Never a secret value."""
        ...


def provenance_sha256(provenance: Mapping[str, str]) -> str:
    """Hash a provenance mapping into one identity digest.

    Key order must not change the digest, so the mapping is serialised sorted.
    """
    payload = json.dumps(dict(provenance), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_input_text(input_text: object, *, target_id: str) -> str:
    """Check the text going into a target at the boundary.

    Raises:
        TargetExecutionError: if it is not a non-empty string within the bound.
            The offending text is never included in the message.
    """
    if not isinstance(input_text, str):
        raise TargetExecutionError(
            f"{target_id}: input must be a string, got {type(input_text).__name__}"
        )
    if not input_text.strip():
        raise TargetExecutionError(
            f"{target_id}: input must contain at least one non-whitespace character"
        )
    if len(input_text) > MAX_INPUT_CHARS:
        raise TargetExecutionError(
            f"{target_id}: input is {len(input_text)} characters, the limit is {MAX_INPUT_CHARS}"
        )
    return input_text


def validate_timeout(timeout_s: object, *, what: str = "timeout_s") -> float:
    """Check a timeout at the boundary.

    Raises:
        TargetConfigError: if it is not a positive, finite number of seconds.
    """
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int | float):
        raise TargetConfigError(f"'{what}' must be a number of seconds, got {timeout_s!r}")
    value = float(timeout_s)
    if value <= 0 or value != value or value == float("inf"):
        raise TargetConfigError(f"'{what}' must be a positive number of seconds, got {value}")
    return value


def validate_name(value: object, *, what: str) -> str:
    """Check a non-empty string setting (a field name, a variable name).

    Raises:
        TargetConfigError: if it is not a non-empty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise TargetConfigError(f"'{what}' must be a non-empty string, got {value!r}")
    return value.strip()


def tail(text: object, limit: int) -> str:
    """The last `limit` characters of `text`, marked when something was dropped.

    Diagnostics from a failing external app are unbounded and end with the part
    that matters, so the tail is kept and the head is dropped.
    """
    if not isinstance(text, str) or not text.strip():
        return "(no output)"
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"…(truncated){stripped[-limit:]}"
