"""Calibrate the judge against human labels.

Stage 02 grades stage 01. Nothing grades stage 02 — so before the judge is
trusted, its verdicts are compared against a human's, criterion by criterion.

The human labels are the ticks a person put in `review.md`; the judge labels are
the verdicts in `verdicts.jsonl`. Both are read deterministically and every
number below is arithmetic, not judgment.

Only the cases the operator names as graded are compared. A case nobody ticked
is ungraded, not failed: reading an untouched checklist as fifteen human "fail"
labels would manufacture a false-fail rate out of nothing.

Two counts matter more than the headline agreement rate:
  - `false_pass` — the judge passed what the human failed. This is the dangerous
    direction: a regression the tool will not report.
  - `false_fail` — the judge failed what the human passed. Noisy, not blind.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .judge_runner import VERDICTS_FILENAME
from .runner import REVIEW_FILENAME, display_path

CALIBRATION_FILENAME = "calibration.json"

CASE_HEADING = re.compile(r"^## (\S+)\s*$")
CHECKBOX = re.compile(r"^- \[([ xX])\]\s+(.+)$")
CRITERIA_HEADING = "### Criteria"

COMPARED_SAMPLE_INDEX = 0
"""Calibration compares the first target sample only."""

COMPARED_JUDGE_SAMPLE_INDEX = 0
"""Calibration compares the first judge sample only."""


class CalibrationError(Exception):
    """Calibration cannot be computed: missing inputs, unknown case, or nothing to compare."""


@dataclass(frozen=True)
class Mismatch:
    """One criterion where the human and the judge disagreed."""

    case_id: str
    criterion_index: int
    criterion: str
    human_passed: bool
    judge_passed: bool
    judge_reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "criterion_index": self.criterion_index,
            "criterion": self.criterion,
            "human": "pass" if self.human_passed else "fail",
            "judge": "pass" if self.judge_passed else "fail",
            "judge_reason": self.judge_reason,
        }


@dataclass(frozen=True)
class CalibrationResult:
    """The comparison between one human's labels and the judge's verdicts."""

    run_id: str
    graded_cases: tuple[str, ...]
    compared: int
    agreements: int
    agreement_rate: float | None
    false_pass: int
    false_fail: int
    judge_errors: int
    not_judged: int
    mismatches: tuple[Mismatch, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "graded_cases": list(self.graded_cases),
            "compared": self.compared,
            "agreements": self.agreements,
            "agreement_rate": self.agreement_rate,
            "false_pass": self.false_pass,
            "false_fail": self.false_fail,
            "judge_errors": self.judge_errors,
            "not_judged": self.not_judged,
            "mismatches": [item.to_json() for item in self.mismatches],
        }


def parse_graded_cases(raw: str) -> tuple[str, ...]:
    """Split the `--graded-cases` value into case ids.

    Raises:
        CalibrationError: if the value names no case. The flag is required
            because "compare everything" would silently treat unticked cases as
            human failures.
    """
    names = tuple(part.strip() for part in str(raw).split(",") if part.strip())
    if not names:
        raise CalibrationError(
            "--graded-cases named no case. Pass the ids you actually graded by hand, "
            "comma separated."
        )
    return names


def parse_review_checkboxes(text: str) -> dict[str, list[bool]]:
    """Read the criterion ticks out of `review.md`, in criterion order per case.

    A box is a pass only when it contains `x` or `X`; anything else is a fail,
    matching the instruction the reviewer is given at the top of the file.
    Fenced blocks are skipped, so a ticket or a summary that happens to contain
    a checkbox line cannot be mistaken for a human label.
    """
    ticks: dict[str, list[bool]] = {}
    case_id: str | None = None
    in_criteria = False
    in_fence = False

    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("###"):
            in_criteria = line.strip() == CRITERIA_HEADING
            continue
        heading = CASE_HEADING.match(line)
        if heading:
            case_id = heading.group(1)
            ticks.setdefault(case_id, [])
            in_criteria = False
            continue
        if not in_criteria or case_id is None:
            continue
        box = CHECKBOX.match(line)
        if box:
            ticks[case_id].append(box.group(1).lower() == "x")

    return ticks


def _read_text(path: Path, *, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CalibrationError(f"{what} not found: {display_path(path)}") from exc
    except OSError as exc:
        raise CalibrationError(f"{what} could not be read: {display_path(path)}") from exc


def _read_judge_labels(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """Index the first judge sample of the first target sample by case and criterion."""
    labels: dict[tuple[str, int], dict[str, Any]] = {}
    for number, line in enumerate(_read_text(path, what="Judge verdicts").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(
                f"{display_path(path)} line {number} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(row, dict):
            raise CalibrationError(f"{display_path(path)} line {number} is not a JSON object")
        if row.get("sample_index") != COMPARED_SAMPLE_INDEX:
            continue
        if row.get("judge_sample_index") != COMPARED_JUDGE_SAMPLE_INDEX:
            continue
        case_id, criterion_index = row.get("case_id"), row.get("criterion_index")
        if not isinstance(case_id, str) or not isinstance(criterion_index, int):
            raise CalibrationError(
                f"{display_path(path)} line {number}: 'case_id' and 'criterion_index' "
                "are required"
            )
        labels[(case_id, criterion_index)] = row
    return labels


def calibrate(*, run_dir: Path, graded_cases: tuple[str, ...]) -> CalibrationResult:
    """Compare human ticks against judge verdicts for the named cases.

    Raises:
        CalibrationError: if `review.md` or `verdicts.jsonl` is missing or
            malformed, if a graded case is absent from `review.md`, or if no
            criterion could be compared at all.
    """
    run_dir = Path(run_dir)
    human = parse_review_checkboxes(_read_text(run_dir / REVIEW_FILENAME, what="Run review"))
    judge = _read_judge_labels(run_dir / VERDICTS_FILENAME)

    if not graded_cases:
        raise CalibrationError("No graded cases were given.")
    missing = [case_id for case_id in graded_cases if case_id not in human]
    if missing:
        raise CalibrationError(
            f"{display_path(run_dir / REVIEW_FILENAME)} has no section for graded case(s): "
            f"{', '.join(missing)}"
        )

    compared = agreements = false_pass = false_fail = judge_errors = not_judged = 0
    mismatches: list[Mismatch] = []

    for case_id in graded_cases:
        for criterion_index, human_passed in enumerate(human[case_id]):
            row = judge.get((case_id, criterion_index))
            if row is None:
                not_judged += 1
                continue
            judge_passed = row.get("passed")
            if not isinstance(judge_passed, bool):
                judge_errors += 1
                continue

            compared += 1
            if judge_passed == human_passed:
                agreements += 1
                continue
            if judge_passed:
                false_pass += 1
            else:
                false_fail += 1
            mismatches.append(
                Mismatch(
                    case_id=case_id,
                    criterion_index=criterion_index,
                    criterion=str(row.get("criterion", "")),
                    human_passed=human_passed,
                    judge_passed=judge_passed,
                    judge_reason=row.get("reason"),
                )
            )

    if compared == 0:
        raise CalibrationError(
            f"No criterion could be compared for {', '.join(graded_cases)}. "
            f"{judge_errors} judge error(s) and {not_judged} unjudged criterion/criteria were "
            "excluded. Check that stage 02 ran on this run and that the case ids are right."
        )

    return CalibrationResult(
        run_id=run_dir.name,
        graded_cases=tuple(graded_cases),
        compared=compared,
        agreements=agreements,
        agreement_rate=agreements / compared,
        false_pass=false_pass,
        false_fail=false_fail,
        judge_errors=judge_errors,
        not_judged=not_judged,
        mismatches=tuple(mismatches),
    )


def render_table(result: CalibrationResult) -> str:
    """Render the result as the table printed to the terminal."""
    rate = "—" if result.agreement_rate is None else f"{result.agreement_rate * 100:.1f}%"
    lines = [
        f"Judge calibration — run {result.run_id}",
        f"Graded cases ({len(result.graded_cases)}): {', '.join(result.graded_cases)}",
        "",
        f"  Compared        {result.compared:>5}",
        f"  Agreement       {rate:>5}  ({result.agreements} of {result.compared})",
        f"  false_pass      {result.false_pass:>5}  judge passed, human failed",
        f"  false_fail      {result.false_fail:>5}  judge failed, human passed",
        f"  judge_errors    {result.judge_errors:>5}  excluded from the comparison",
        f"  not_judged      {result.not_judged:>5}  excluded from the comparison",
        "",
    ]

    if not result.mismatches:
        lines.append("No mismatches.")
        return "\n".join(lines)

    lines.append(f"Mismatches ({len(result.mismatches)}):")
    for item in result.mismatches:
        human = "pass" if item.human_passed else "fail"
        judge = "pass" if item.judge_passed else "fail"
        lines.extend(
            [
                f"  {item.case_id} [{item.criterion_index + 1}] {item.criterion}",
                f"      human: {human}   judge: {judge}",
                f"      judge reason: {item.judge_reason}",
            ]
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calibrate",
        description="Compare the judge's verdicts against a human's ticks in review.md.",
    )
    parser.add_argument(
        "--run", type=Path, required=True, help="The run directory holding review.md and verdicts."
    )
    parser.add_argument(
        "--graded-cases",
        required=True,
        help=(
            "Comma-separated ids of the cases a human actually graded. Required: cases you "
            "did not grade are ungraded, not failed."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    try:
        result = calibrate(run_dir=args.run, graded_cases=parse_graded_cases(args.graded_cases))
    except CalibrationError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    destination = Path(args.run) / CALIBRATION_FILENAME
    destination.write_text(
        json.dumps(result.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(render_table(result))
    print(f"\nWritten: {display_path(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
