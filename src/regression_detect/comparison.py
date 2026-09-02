"""The record stage 03 produces, and the sentence it reads out loud.

`compare.py` does the statistics; this module holds what those statistics
*are* — the per-criterion, per-case and overall rows, the verdict enum, the JSON
shape written to `comparison.json`, and `explain()`, the plain-English reasoning
a human sees on the pull request.

Everything here is data and formatting. No decision is taken in this file.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

COMPARISON_FILENAME = "comparison.json"
SCHEMA_VERSION = 1


class Verdict(StrEnum):
    """What stage 03 concluded. A `StrEnum`, so it serialises as its own name."""

    REGRESSION = "REGRESSION"
    NO_REGRESSION = "NO_REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"


EXIT_CODES: dict[Verdict, int] = {
    Verdict.NO_REGRESSION: 0,
    Verdict.REGRESSION: 1,
    Verdict.INCONCLUSIVE: 2,
}
INPUT_ERROR_EXIT_CODE = 3
"""Reserved for a broken baseline, run directory or config — never for a verdict.
CI must be able to tell "the feature got worse" from "the tool could not run"."""


def _rate(passes: int, n: int) -> float | None:
    """`None`, never zero, when nothing was judged: no evidence is not failure."""
    return (passes / n) if n else None


def _difference(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    return candidate - baseline


@dataclass(frozen=True)
class CriterionComparison:
    """One criterion, on both sides."""

    case_id: str
    criterion_index: int
    criterion: str
    baseline_passes: int
    baseline_n: int
    candidate_passes: int
    candidate_n: int
    hard_regression: bool

    @property
    def baseline_rate(self) -> float | None:
        return _rate(self.baseline_passes, self.baseline_n)

    @property
    def candidate_rate(self) -> float | None:
        return _rate(self.candidate_passes, self.candidate_n)

    @property
    def difference(self) -> float | None:
        return _difference(self.baseline_rate, self.candidate_rate)

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "criterion_index": self.criterion_index,
            "criterion": self.criterion,
            "baseline": {"passes": self.baseline_passes, "n": self.baseline_n},
            "candidate": {"passes": self.candidate_passes, "n": self.candidate_n},
            "difference": self.difference,
            "hard_regression": self.hard_regression,
        }


@dataclass(frozen=True)
class CaseComparison:
    """One golden case, pooled over its matched criteria."""

    case_id: str
    baseline_passes: int
    baseline_n: int
    candidate_passes: int
    candidate_n: int

    @property
    def baseline_rate(self) -> float | None:
        return _rate(self.baseline_passes, self.baseline_n)

    @property
    def candidate_rate(self) -> float | None:
        return _rate(self.candidate_passes, self.candidate_n)

    @property
    def difference(self) -> float | None:
        return _difference(self.baseline_rate, self.candidate_rate)

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "baseline": {"passes": self.baseline_passes, "n": self.baseline_n},
            "candidate": {"passes": self.candidate_passes, "n": self.candidate_n},
            "difference": self.difference,
        }


@dataclass(frozen=True)
class UnmatchedCriterion:
    """A criterion present on one side only — reported, never silently dropped."""

    case_id: str
    criterion_index: int
    criterion: str
    present_in: str

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "criterion_index": self.criterion_index,
            "criterion": self.criterion,
            "present_in": self.present_in,
        }


@dataclass(frozen=True)
class ComparisonResult:
    """The whole comparison: the numbers, the thresholds, the verdict, and why."""

    verdict: Verdict
    baseline_passes: int
    baseline_n: int
    baseline_interval: tuple[float, float]
    candidate_passes: int
    candidate_n: int
    candidate_interval: tuple[float, float]
    p_value: float
    alpha: float
    min_effect: float
    min_samples: int
    max_judge_error_rate: float
    candidate_judge_errors: int
    candidate_judge_rows: int
    criteria: tuple[CriterionComparison, ...]
    cases: tuple[CaseComparison, ...]
    unmatched: tuple[UnmatchedCriterion, ...]

    @property
    def baseline_rate(self) -> float | None:
        return _rate(self.baseline_passes, self.baseline_n)

    @property
    def candidate_rate(self) -> float | None:
        return _rate(self.candidate_passes, self.candidate_n)

    @property
    def difference(self) -> float | None:
        """Candidate minus baseline, as a fraction. Negative is a drop."""
        return _difference(self.baseline_rate, self.candidate_rate)

    @property
    def drop(self) -> float:
        """How far the rate fell; zero when it held or rose."""
        difference = self.difference
        return -difference if difference is not None and difference < 0 else 0.0

    @property
    def candidate_judge_error_rate(self) -> float:
        if not self.candidate_judge_rows:
            return 0.0
        return self.candidate_judge_errors / self.candidate_judge_rows

    @property
    def hard_regressions(self) -> tuple[CriterionComparison, ...]:
        return tuple(row for row in self.criteria if row.hard_regression)

    @property
    def flagged(self) -> tuple[CriterionComparison, ...]:
        """The rows worth printing: anything that fell, and every hard regression."""
        return tuple(
            row
            for row in self.criteria
            if row.hard_regression or (row.difference is not None and row.difference < 0)
        )

    @property
    def statistically_significant(self) -> bool:
        """Both halves of the statistical rule: big enough, and unlikely enough."""
        return self.drop >= self.min_effect and self.p_value < self.alpha

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]

    def explain(self) -> str:
        """The plain-English reasoning the report shows a human."""
        return explain(self)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "verdict": self.verdict.value,
            "explanation": self.explain(),
            "overall": {
                "baseline": {
                    "passes": self.baseline_passes,
                    "n": self.baseline_n,
                    "rate": self.baseline_rate,
                    "wilson_95": list(self.baseline_interval),
                },
                "candidate": {
                    "passes": self.candidate_passes,
                    "n": self.candidate_n,
                    "rate": self.candidate_rate,
                    "wilson_95": list(self.candidate_interval),
                },
                "difference": self.difference,
            },
            "p_value": self.p_value,
            "thresholds": {
                "alpha": self.alpha,
                "min_effect": self.min_effect,
                "min_samples": self.min_samples,
                "max_judge_error_rate": self.max_judge_error_rate,
            },
            "candidate_judge_errors": {
                "errors": self.candidate_judge_errors,
                "rows": self.candidate_judge_rows,
                "rate": self.candidate_judge_error_rate,
            },
            "cases": [case.to_json() for case in self.cases],
            "criteria": [row.to_json() for row in self.criteria],
            "unmatched": [item.to_json() for item in self.unmatched],
        }


def percent(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.1f}%"


def _points(fraction: float) -> str:
    return f"{fraction * 100:.1f}"


def _threshold_points(fraction: float) -> str:
    """A threshold reads better without trailing zeros: 5, not 5.0."""
    return f"{fraction * 100:g}"


def _p_text(value: float) -> str:
    return f"{value:.4f}" if value < 0.001 else f"{value:.3f}"


def _criteria_word(count: int) -> str:
    return "criterion" if count == 1 else "criteria"


def _movement(result: ComparisonResult) -> str:
    baseline, candidate = result.baseline_rate, result.candidate_rate
    if baseline is None or candidate is None:
        return (
            f"No pass rate could be compared: {result.baseline_n} baseline and "
            f"{result.candidate_n} candidate judged criteria matched"
        )

    before = f"{percent(baseline)} ({result.baseline_passes}/{result.baseline_n})"
    after = f"{percent(candidate)} ({result.candidate_passes}/{result.candidate_n})"

    difference = result.difference or 0.0
    if difference < 0:
        return f"Pass rate fell from {before} to {after}, a drop of {_points(result.drop)} points"
    if difference > 0:
        return f"Pass rate rose from {before} to {after}, a rise of {_points(difference)} points"
    return (
        f"Pass rate held at {percent(baseline)} "
        f"({result.baseline_passes}/{result.baseline_n} baseline, "
        f"{result.candidate_passes}/{result.candidate_n} candidate)"
    )


def _regression_reasons(result: ComparisonResult) -> str:
    reasons = []
    hard = result.hard_regressions
    if hard:
        reasons.append(
            f"{len(hard)} {_criteria_word(len(hard))} the baseline always passed now "
            "always fail"
        )
    if result.statistically_significant:
        reasons.append(
            f"one-sided Fisher exact p = {_p_text(result.p_value)} < {result.alpha:g} and the "
            f"drop exceeds the {_threshold_points(result.min_effect)}-point minimum effect"
        )
    return " and ".join(reasons)


def _inconclusive_reasons(result: ComparisonResult) -> str:
    reasons = []
    if result.baseline_n == 0 or result.candidate_n == 0:
        reasons.append("no criterion matched between the baseline and the candidate")
    elif result.candidate_n < result.min_samples:
        reasons.append(
            f"the candidate judged only {result.candidate_n} criteria, below the "
            f"{result.min_samples} needed"
        )
    if result.candidate_judge_error_rate > result.max_judge_error_rate:
        reasons.append(
            f"the judge failed on {result.candidate_judge_errors} of "
            f"{result.candidate_judge_rows} candidate rows "
            f"({_points(result.candidate_judge_error_rate)}%), above the "
            f"{_threshold_points(result.max_judge_error_rate)}% ceiling"
        )
    return " and ".join(reasons)


def _no_regression_reasons(result: ComparisonResult) -> str:
    if result.drop == 0.0:
        return "the pass rate did not fall"
    reasons = []
    if result.drop < result.min_effect:
        reasons.append(
            f"the drop is below the {_threshold_points(result.min_effect)}-point minimum effect"
        )
    if result.p_value >= result.alpha:
        reasons.append(
            f"one-sided Fisher exact p = {_p_text(result.p_value)} is not below "
            f"{result.alpha:g}"
        )
    return " and ".join(reasons)


def explain(result: ComparisonResult) -> str:
    """Turn a result into the sentence the report shows.

    Every number the verdict rests on appears in the text, so a reader never has
    to open `comparison.json` to check the reasoning.
    """
    reasons = {
        Verdict.REGRESSION: _regression_reasons,
        Verdict.INCONCLUSIVE: _inconclusive_reasons,
        Verdict.NO_REGRESSION: _no_regression_reasons,
    }[result.verdict](result)

    sentence = f"{_movement(result)}; {reasons} → {result.verdict.value}."
    if result.unmatched:
        count = len(result.unmatched)
        sentence += (
            f" {count} {_criteria_word(count)} appear on only one side (the goldens changed) "
            "and were excluded from every number above."
        )
    return sentence
