"""Render a run into `review.md`, the file a human grades by hand.

Stage 01 does not judge anything. It produces evidence a person can read: the
input, what the model said about it, and the criteria as an unticked checklist.
The human grader is the verifier for this stage.
"""

from dataclasses import dataclass
from typing import Any

from .goldens import GoldenCase

REVIEW_INPUT_MAX_CHARS = 600
"""Tickets longer than this are shown truncated, with a note saying so."""


@dataclass(frozen=True)
class SampleResult:
    """One model call: its output, or the error that replaced it."""

    case_id: str
    sample_index: int
    output: str | None
    model_id: str
    prompt_sha256: str
    latency_ms: int
    error: str | None = None
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_json_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "sample_index": self.sample_index,
            "output": self.output,
            "model_id": self.model_id,
            "prompt_sha256": self.prompt_sha256,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "error_type": self.error_type,
        }


def _fenced(text: str) -> str:
    """Fence a block of text, using a longer fence if the text contains one."""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text.rstrip()}\n{fence}"


def _render_input(case: GoldenCase) -> str:
    if len(case.input) <= REVIEW_INPUT_MAX_CHARS:
        return _fenced(case.input)
    head = case.input[:REVIEW_INPUT_MAX_CHARS]
    dropped = len(case.input) - REVIEW_INPUT_MAX_CHARS
    note = (
        f"\n\n_Input truncated for review: showing the first {REVIEW_INPUT_MAX_CHARS} "
        f"of {len(case.input)} characters ({dropped} not shown). The full ticket was "
        f"sent to the model; read it in the goldens file._"
    )
    return _fenced(head) + note


def _render_case(case: GoldenCase, results: list[SampleResult]) -> str:
    lines = [f"## {case.id}", ""]
    if case.tags:
        lines.append(f"Tags: {', '.join(case.tags)}")
    if case.notes:
        lines.append(f"Why this case exists: {case.notes}")
    lines.extend(["", "### Input", "", _render_input(case), ""])

    lines.append("### Output")
    lines.append("")
    if not results:
        lines.extend(["_No samples were run for this case._", ""])
    for result in sorted(results, key=lambda item: item.sample_index):
        if len(results) > 1:
            lines.append(f"**Sample {result.sample_index + 1} of {len(results)}**")
            lines.append("")
        if result.ok and result.output is not None:
            lines.append(_fenced(result.output))
        else:
            lines.append(f"**FAILED** — `{result.error_type}`: {result.error}")
        lines.append("")

    lines.extend(["### Criteria", ""])
    lines.extend(f"- [ ] {criterion}" for criterion in case.criteria)
    lines.append("")
    return "\n".join(lines)


def render_review(
    cases: list[GoldenCase],
    results: list[SampleResult],
    manifest: dict[str, Any],
) -> str:
    """Build the whole `review.md` document."""
    by_case: dict[str, list[SampleResult]] = {case.id: [] for case in cases}
    for result in results:
        by_case.setdefault(result.case_id, []).append(result)

    counts = manifest["counts"]
    header = [
        "# Golden run review — stage 01",
        "",
        "Grade by hand. Tick a criterion only if the output above it satisfies the",
        "criterion as written. An unticked box is a fail, not a maybe.",
        "",
        f"- Run: `{manifest['run_id']}`",
        f"- Model: `{manifest['model_id']}`",
        f"- Goldens: `{manifest['goldens_path']}` (sha256 `{manifest['goldens_sha256'][:12]}…`)",
        f"- Prompt: `{manifest['prompt_path']}` (sha256 `{manifest['prompt_sha256'][:12]}…`)",
        f"- Samples per case: {manifest['samples']}",
        f"- Started: {manifest['started_at_utc']} · Finished: {manifest['finished_at_utc']}",
        f"- Calls: {counts['total']} total, {counts['ok']} ok, {counts['failed']} failed",
        "",
        "---",
        "",
    ]

    body = [_render_case(case, by_case.get(case.id, [])) for case in cases]
    return "\n".join(header) + "\n---\n\n".join(body)
