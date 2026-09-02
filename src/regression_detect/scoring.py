"""Stage 02's records and arithmetic: verdict rows, scores, and `judged.md`.

Everything here is deterministic. The judge decides one criterion at a time and
nothing else; turning those decisions into counts and pass rates is arithmetic,
so it is done in code and never asked of a model.

A verdict has three outcomes, not two: passed, failed, and errored. An errored
criterion is excluded from the pass rate rather than counted as a failure — a
judge that could not be read is missing evidence, not evidence of a regression.
"""

from dataclasses import dataclass
from typing import Any

from .goldens import GoldenCase
from .judge_inputs import OutputRow
from .review import fence_text

PASS_MARK = "✅"
FAIL_MARK = "❌"
ERROR_MARK = "⚠️"


@dataclass(frozen=True)
class VerdictRow:
    """One judge call about one criterion: its verdict, or the error that replaced it."""

    case_id: str
    sample_index: int
    criterion_index: int
    criterion: str
    judge_sample_index: int
    passed: bool | None
    reason: str | None
    judge_model_id: str
    judge_prompt_sha256: str
    latency_ms: int
    error_type: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_json_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "sample_index": self.sample_index,
            "criterion_index": self.criterion_index,
            "criterion": self.criterion,
            "judge_sample_index": self.judge_sample_index,
            "passed": self.passed,
            "reason": self.reason,
            "judge_model_id": self.judge_model_id,
            "judge_prompt_sha256": self.judge_prompt_sha256,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error": self.error,
        }


def _tally(verdicts: list[VerdictRow]) -> dict[str, Any]:
    passed = sum(1 for verdict in verdicts if verdict.passed is True)
    failed = sum(1 for verdict in verdicts if verdict.passed is False)
    errored = sum(1 for verdict in verdicts if verdict.passed is None)
    judged = passed + failed
    return {
        "criteria_total": passed + failed + errored,
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "pass_rate": (passed / judged) if judged else None,
    }


def build_scores(
    *, run_id: str, cases: list[GoldenCase], verdicts: list[VerdictRow]
) -> dict[str, Any]:
    """Count the verdicts per case and overall. `pass_rate` is `passed / (passed + failed)`.

    A case with nothing judged gets a null pass rate, never a zero: "no evidence"
    and "failed everything" must not look the same to stage 03.
    """
    by_case: dict[str, list[VerdictRow]] = {case.id: [] for case in cases}
    for verdict in verdicts:
        by_case.setdefault(verdict.case_id, []).append(verdict)

    return {
        "run_id": run_id,
        "cases": {case_id: _tally(rows) for case_id, rows in by_case.items()},
        "overall": _tally(list(verdicts)),
    }


def _percent(rate: float | None) -> str:
    return "not judged" if rate is None else f"{rate * 100:.1f}%"


def _render_verdict(verdict: VerdictRow, *, judge_samples: int) -> list[str]:
    label = f"[{verdict.criterion_index + 1}] {verdict.criterion}"
    if judge_samples > 1:
        label = f"{label} _(judge sample {verdict.judge_sample_index + 1})_"

    if verdict.passed is None:
        return [
            f"- {ERROR_MARK} {label}",
            f"  - _Not judged — `{verdict.error_type}`: {verdict.error}_",
        ]
    mark = PASS_MARK if verdict.passed else FAIL_MARK
    return [f"- {mark} {label}", f"  - _{verdict.reason}_"]


def _render_sample(
    output: OutputRow, verdicts: list[VerdictRow], *, sample_count: int, judge_samples: int
) -> list[str]:
    lines: list[str] = []
    if sample_count > 1:
        lines.extend([f"**Sample {output.sample_index + 1} of {sample_count}**", ""])

    lines.extend(["### Output", ""])
    if output.output is None:
        lines.extend(
            [
                f"_Stage 01 recorded no output for this sample "
                f"(`{output.error_type}`: {output.error}); not judged._",
                "",
            ]
        )
        return lines
    lines.extend([fence_text(output.output), "", "### Verdicts", ""])

    if not verdicts:
        lines.extend(["_No criteria were judged for this sample._", ""])
        return lines
    for verdict in verdicts:
        lines.extend(_render_verdict(verdict, judge_samples=judge_samples))
    lines.append("")
    return lines


def _render_case(
    case: GoldenCase,
    outputs: list[OutputRow],
    verdicts: list[VerdictRow],
    *,
    scores: dict[str, Any],
    judge_samples: int,
) -> str:
    tally = scores["cases"][case.id]
    lines = [
        f"## {case.id}",
        "",
        f"Pass rate: {_percent(tally['pass_rate'])} — {tally['passed']} passed, "
        f"{tally['failed']} failed, {tally['errored']} errored",
        "",
    ]

    if not outputs:
        return "\n".join([*lines, "_Stage 01 ran no samples for this case._", ""])

    ordered = sorted(outputs, key=lambda row: row.sample_index)
    for output in ordered:
        for_sample = sorted(
            (
                verdict
                for verdict in verdicts
                if verdict.sample_index == output.sample_index
            ),
            key=lambda verdict: (verdict.criterion_index, verdict.judge_sample_index),
        )
        lines.extend(
            _render_sample(
                output, for_sample, sample_count=len(ordered), judge_samples=judge_samples
            )
        )
    return "\n".join(lines)


def render_judged(
    *,
    cases: list[GoldenCase],
    outputs: list[OutputRow],
    verdicts: list[VerdictRow],
    scores: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    """Build the whole `judged.md` document: per case, the output and its verdicts."""
    outputs_by_case: dict[str, list[OutputRow]] = {case.id: [] for case in cases}
    for output in outputs:
        outputs_by_case.setdefault(output.case_id, []).append(output)

    verdicts_by_case: dict[str, list[VerdictRow]] = {case.id: [] for case in cases}
    for verdict in verdicts:
        verdicts_by_case.setdefault(verdict.case_id, []).append(verdict)

    counts = manifest["counts"]
    overall = scores["overall"]
    judge_samples = manifest["judge_samples"]
    prompt_hash = manifest["judge_prompt_sha256"][:12]
    header = [
        "# Judge report — stage 02",
        "",
        "Machine-graded. Read it beside `review.md`: where the human tick and the",
        "machine verdict for the same criterion disagree, the judge is the thing",
        "under suspicion, not the target. `calibrate.py` counts those disagreements.",
        "",
        f"- Run: `{manifest['run_id']}`",
        f"- Judge model: `{manifest['judge_model_id']}`",
        f"- Judge prompt: `{manifest['judge_prompt_path']}` (sha256 `{prompt_hash}…`)",
        f"- Goldens sha256: `{manifest['goldens_sha256'][:12]}…`",
        f"- Judge samples per criterion: {judge_samples}",
        f"- Started: {manifest['started_at_utc']} · Finished: {manifest['finished_at_utc']}",
        f"- Verdicts: {counts['verdicts_ok']} ok, {counts['judge_errors']} judge error(s), "
        f"{counts['skipped_outputs']} output(s) skipped",
        f"- Overall pass rate: {_percent(overall['pass_rate'])} "
        f"({overall['passed']} of {overall['passed'] + overall['failed']} judged criteria)",
        "",
        "---",
        "",
    ]

    body = [
        _render_case(
            case,
            outputs_by_case.get(case.id, []),
            verdicts_by_case.get(case.id, []),
            scores=scores,
            judge_samples=judge_samples,
        )
        for case in cases
    ]
    return "\n".join(header) + "\n---\n\n".join(body)
