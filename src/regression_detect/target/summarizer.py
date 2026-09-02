"""The target feature under regression test: summarize one support ticket.

Two boundaries are validated here, because both carry untrusted data:
  - the ticket coming in (a customer wrote it);
  - the summary coming back (a model wrote it).

The ticket is never formatted into the system prompt. It travels as the user
message inside `<ticket>` delimiters, so prompt text and customer text stay
separable no matter what the customer typed.
"""

import hashlib
from pathlib import Path

from ..providers.base import Provider

DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "summarize_v1.md"
"""Resolved relative to this package, so a fresh clone works anywhere."""

MAX_TICKET_CHARS = 20_000
"""Upper bound on one ticket. Bounds the request size and the cost per call."""

TICKET_OPEN_TAG = "<ticket>"
TICKET_CLOSE_TAG = "</ticket>"


class SummarizerError(Exception):
    """Base class for failures in the ticket summarizer."""


class InvalidTicketError(SummarizerError, ValueError):
    """The ticket is not usable input: wrong type, empty, or whitespace only."""


class TicketTooLongError(InvalidTicketError):
    """The ticket exceeds `MAX_TICKET_CHARS`."""


class SummaryValidationError(SummarizerError):
    """The model's reply failed validation and must not be used."""


def load_prompt(prompt_path: Path = DEFAULT_PROMPT_PATH) -> str:
    """Read the system prompt from disk.

    Raises:
        FileNotFoundError: if the prompt file is missing. A missing prompt is a
            broken install, never a silent fallback to no instructions.
    """
    return Path(prompt_path).read_text(encoding="utf-8")


def prompt_sha256(prompt_path: Path = DEFAULT_PROMPT_PATH) -> str:
    """Hash of the prompt file, recorded in run manifests to pin the version."""
    return hashlib.sha256(Path(prompt_path).read_bytes()).hexdigest()


def build_user_message(ticket: str) -> str:
    """Wrap the ticket in delimiters as the user message."""
    return f"{TICKET_OPEN_TAG}\n{ticket}\n{TICKET_CLOSE_TAG}"


def _validate_ticket(ticket: object) -> str:
    if not isinstance(ticket, str):
        raise InvalidTicketError(f"ticket must be a string, got {type(ticket).__name__}")
    if not ticket.strip():
        raise InvalidTicketError("ticket must contain at least one non-whitespace character")
    if len(ticket) > MAX_TICKET_CHARS:
        raise TicketTooLongError(
            f"ticket is {len(ticket)} characters, the limit is {MAX_TICKET_CHARS}"
        )
    return ticket


def _validate_summary(summary: object) -> str:
    if not isinstance(summary, str):
        raise SummaryValidationError(
            f"provider returned {type(summary).__name__}, expected a string"
        )
    stripped = summary.strip()
    if not stripped:
        raise SummaryValidationError("provider returned an empty summary")
    return stripped


def summarize(
    ticket: str,
    provider: Provider,
    *,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    temperature: float = 0.2,
) -> str:
    """Summarize one support ticket for a support agent.

    Args:
        ticket: Raw ticket text as the customer wrote it.
        provider: The model adapter to call.
        prompt_path: System prompt file; defaults to the packaged v1 prompt.
        temperature: Sampling temperature passed to the provider.

    Returns:
        The stripped summary text.

    Raises:
        InvalidTicketError: the ticket is empty, whitespace only, or not a string.
        TicketTooLongError: the ticket exceeds `MAX_TICKET_CHARS`.
        FileNotFoundError: the prompt file is missing.
        SummaryValidationError: the provider returned an unusable reply.
        ProviderError: the provider call itself failed.
    """
    validated_ticket = _validate_ticket(ticket)
    system_prompt = load_prompt(prompt_path)

    raw_summary = provider.complete(
        system=system_prompt,
        user=build_user_message(validated_ticket),
        temperature=temperature,
    )

    return _validate_summary(raw_summary)
