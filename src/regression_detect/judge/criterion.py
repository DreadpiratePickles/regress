"""Judge one summary against one criterion, and validate what comes back.

Three boundaries carry untrusted data and all three are validated here:
  - the ticket (a customer wrote it);
  - the summary (the target model wrote it);
  - the verdict (the judge model wrote it).

None of the three is ever formatted into the system prompt. They travel as the
user message inside `<ticket>`, `<summary>` and `<criterion>` delimiters, so
grader instructions and graded material stay separable whatever they contain.

A verdict that fails validation raises `JudgeParseError`. It never degrades into
`passed=False`: "the judge could not be read" and "the criterion was not met"
are different facts and the difference has to survive into the scores.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..providers.base import Provider

DEFAULT_JUDGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "judge_v1.md"
"""Resolved relative to this package, so a fresh clone works anywhere."""

VERDICT_KEYS = frozenset({"reason", "passed"})

TICKET_OPEN_TAG, TICKET_CLOSE_TAG = "<ticket>", "</ticket>"
SUMMARY_OPEN_TAG, SUMMARY_CLOSE_TAG = "<summary>", "</summary>"
CRITERION_OPEN_TAG, CRITERION_CLOSE_TAG = "<criterion>", "</criterion>"

FENCE_CHARACTER = "`"
MIN_FENCE_LENGTH = 3


class JudgeError(Exception):
    """Base class for failures in the criterion judge."""


class InvalidJudgeInputError(JudgeError, ValueError):
    """A field handed to the judge is not usable: wrong type, empty, or blank."""


class JudgeParseError(JudgeError, ValueError):
    """The judge's reply is not a valid verdict and must not be used."""


@dataclass(frozen=True)
class Verdict:
    """One judge decision about one criterion."""

    passed: bool
    reason: str


def load_judge_prompt(prompt_path: Path = DEFAULT_JUDGE_PROMPT_PATH) -> str:
    """Read the judge system prompt from disk.

    Raises:
        FileNotFoundError: if the prompt file is missing. A missing prompt is a
            broken install, never a silent fallback to an unguided judge.
    """
    return Path(prompt_path).read_text(encoding="utf-8")


def judge_prompt_sha256(prompt_path: Path = DEFAULT_JUDGE_PROMPT_PATH) -> str:
    """Hash of the judge prompt, recorded in the judge manifest to pin the version."""
    return hashlib.sha256(Path(prompt_path).read_bytes()).hexdigest()


def build_judge_user_message(ticket: str, summary: str, criterion: str) -> str:
    """Wrap the three inputs in delimiters as the user message."""
    return (
        f"{TICKET_OPEN_TAG}\n{ticket}\n{TICKET_CLOSE_TAG}\n\n"
        f"{SUMMARY_OPEN_TAG}\n{summary}\n{SUMMARY_CLOSE_TAG}\n\n"
        f"{CRITERION_OPEN_TAG}\n{criterion}\n{CRITERION_CLOSE_TAG}"
    )


def _strip_one_fence(text: str) -> str:
    """Remove a single surrounding markdown fence, if the reply is wrapped in one.

    Tolerated because a fence is the one deviation models produce constantly and
    it changes nothing about the payload. Anything else is a parse failure.
    """
    if not text.startswith(FENCE_CHARACTER * MIN_FENCE_LENGTH):
        return text

    opening, _, remainder = text.partition("\n")
    fence = opening[: len(opening) - len(opening.lstrip(FENCE_CHARACTER))]
    language = opening[len(fence) :].strip()
    if language and language != "json":
        return text
    if not remainder.rstrip().endswith(fence):
        return text
    closed = remainder.rstrip()
    return closed[: len(closed) - len(fence)].strip()


def parse_verdict(raw: str) -> Verdict:
    """Parse the judge's reply into a `Verdict`.

    Tolerant of surrounding whitespace and of a single markdown fence. Strict
    about everything else: exactly the keys `reason` and `passed`, a real JSON
    boolean, and a non-empty reason.

    Raises:
        JudgeParseError: on a non-string reply, non-JSON text, a non-object,
            a missing key, an extra key, a non-boolean `passed`, or an empty
            or non-string `reason`.
    """
    if not isinstance(raw, str):
        raise JudgeParseError(f"judge reply must be a string, got {type(raw).__name__}")

    candidate = _strip_one_fence(raw.strip()).strip()
    if not candidate:
        raise JudgeParseError("judge returned an empty reply")

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise JudgeParseError(
            f"judge reply is not JSON: {exc.msg} (at position {exc.pos})"
        ) from exc

    if not isinstance(payload, dict):
        raise JudgeParseError(
            f"judge reply must be a JSON object, got {type(payload).__name__}"
        )

    keys = set(payload)
    if keys != VERDICT_KEYS:
        missing = sorted(VERDICT_KEYS - keys)
        extra = sorted(keys - VERDICT_KEYS)
        raise JudgeParseError(
            f"judge reply must have exactly the keys 'reason' and 'passed' "
            f"(missing: {missing or 'none'}; unexpected: {extra or 'none'})"
        )

    passed = payload["passed"]
    if not isinstance(passed, bool):
        raise JudgeParseError(
            f"'passed' must be a JSON boolean, got {type(passed).__name__}: {passed!r}"
        )

    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise JudgeParseError("'reason' must be a non-empty string")

    return Verdict(passed=passed, reason=reason.strip())


def _validate_field(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidJudgeInputError(f"{field} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise InvalidJudgeInputError(f"{field} must contain at least one non-whitespace character")
    return value


def judge_criterion(
    *,
    ticket: str,
    summary: str,
    criterion: str,
    provider: Provider,
    prompt_path: Path = DEFAULT_JUDGE_PROMPT_PATH,
    temperature: float = 0.0,
) -> Verdict:
    """Ask the judge whether `summary` satisfies `criterion` for `ticket`.

    Args:
        ticket: The raw ticket the summary was made from.
        summary: The candidate summary produced by the target feature.
        criterion: One plain-English pass criterion from the golden case.
        provider: The model adapter to call.
        prompt_path: Judge system prompt; defaults to the packaged v1 prompt.
        temperature: Sampling temperature; 0.0 so a verdict is as stable as the
            provider allows.

    Returns:
        The parsed `Verdict`.

    Raises:
        InvalidJudgeInputError: any of the three fields is blank or not a string.
        FileNotFoundError: the judge prompt file is missing.
        JudgeParseError: the reply is not a valid verdict.
        ProviderError: the provider call itself failed.
    """
    validated_ticket = _validate_field(ticket, field="ticket")
    validated_summary = _validate_field(summary, field="summary")
    validated_criterion = _validate_field(criterion, field="criterion")
    system_prompt = load_judge_prompt(prompt_path)

    raw_verdict = provider.complete(
        system=system_prompt,
        user=build_judge_user_message(validated_ticket, validated_summary, validated_criterion),
        temperature=temperature,
    )

    return parse_verdict(raw_verdict)
