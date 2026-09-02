"""Stage 03, part two: decide whether a drop is a regression or run-to-run noise.

This module is pure. It opens no file, reads no clock, calls no model, and holds
no configuration of its own: two `Baseline` objects and four thresholds go in, a
`ComparisonResult` comes out. That is what makes the verdict reproducible — the
same two baselines always yield the same verdict and the same sentence.

The question is a two-by-two table, not a threshold on one number:

                  passed   failed
    baseline        65        2
    candidate       55       12

`fisher_exact_one_sided` asks how often chance alone would deal a candidate row
this bad, given the margins. `wilson_interval` says how wide the uncertainty
around each rate really is. `min_effect` then asks the separate question a
p-value cannot answer: is the drop big enough to care about.

The record types and the English rendering live in `comparison.py`; the reasons
behind each choice are written up in `docs/statistics.md`.
"""

import math
from dataclasses import replace

from .baseline import Baseline
from .comparison import (
    COMPARISON_FILENAME,
    EXIT_CODES,
    INPUT_ERROR_EXIT_CODE,
    CaseComparison,
    ComparisonResult,
    CriterionComparison,
    UnmatchedCriterion,
    Verdict,
)

DEFAULT_ALPHA = 0.05
DEFAULT_MIN_EFFECT = 0.05
DEFAULT_MIN_SAMPLES = 30
DEFAULT_MAX_JUDGE_ERROR_RATE = 0.2

WILSON_Z = 1.96
"""Standard normal quantile for a 95% interval."""

HARD_REGRESSION_MIN_N = 2
"""A criterion needs this many verdicts on each side before "always passed, now
always fails" means anything. At n = 1 it is one coin landing the other way up."""

__all__ = [
    "COMPARISON_FILENAME",
    "DEFAULT_ALPHA",
    "DEFAULT_MAX_JUDGE_ERROR_RATE",
    "DEFAULT_MIN_EFFECT",
    "DEFAULT_MIN_SAMPLES",
    "EXIT_CODES",
    "HARD_REGRESSION_MIN_N",
    "INPUT_ERROR_EXIT_CODE",
    "WILSON_Z",
    "CaseComparison",
    "ComparisonResult",
    "CriterionComparison",
    "UnmatchedCriterion",
    "Verdict",
    "compare",
    "fisher_exact_one_sided",
    "wilson_interval",
]
"""The stage's public surface. The record types are re-exported from
`comparison` so a caller only has to know about the comparison, not its parts."""


def _check_counts(**counts: int) -> None:
    for name, value in counts.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer, got {value!r}")
        if value < 0:
            raise ValueError(f"{name} must not be negative, got {value}")


def fisher_exact_one_sided(a_pass: int, a_n: int, b_pass: int, b_n: int) -> float:
    """One-sided Fisher exact p-value that the candidate's pass rate is lower.

    Rows are baseline (`a`) and candidate (`b`); columns are pass and fail.
    Conditioning on both margins, the candidate's pass count under the null
    "both rows come from one and the same rate" is hypergeometric, so the
    p-value is the lower tail: the chance of dealing a candidate row this bad or
    worse when nothing actually changed.

    Computed with `math.comb` in exact integer arithmetic — no numerical library,
    and no normal approximation that would misbehave at the small counts and
    near-1.0 rates this tool actually sees.

    Args:
        a_pass: Baseline passes.
        a_n: Baseline judged criteria.
        b_pass: Candidate passes.
        b_n: Candidate judged criteria.

    Returns:
        A probability in [0, 1]. Exactly `1.0` when either side has no samples:
        absence of evidence must never read as evidence of a regression.

    Raises:
        ValueError: if a count is negative or a pass count exceeds its n.
    """
    _check_counts(a_pass=a_pass, a_n=a_n, b_pass=b_pass, b_n=b_n)
    if a_pass > a_n:
        raise ValueError(f"a_pass ({a_pass}) cannot exceed a_n ({a_n})")
    if b_pass > b_n:
        raise ValueError(f"b_pass ({b_pass}) cannot exceed b_n ({b_n})")

    if a_n == 0 or b_n == 0:
        return 1.0

    total = a_n + b_n
    passes = a_pass + b_pass
    lowest = max(0, b_n - (total - passes))
    numerator = sum(
        math.comb(passes, observed) * math.comb(total - passes, b_n - observed)
        for observed in range(lowest, b_pass + 1)
    )
    return min(1.0, max(0.0, numerator / math.comb(total, b_n)))


def wilson_interval(passes: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval for a pass rate.

    Preferred over the textbook normal interval because pass rates here sit close
    to 1.0 with small n — exactly where the normal interval runs past 100% or
    collapses to a single point at 0/n and n/n.

    Returns:
        `(low, high)`, clamped to [0, 1]. `(0.0, 1.0)` when `n` is zero: with no
        samples, every rate is still on the table.

    Raises:
        ValueError: if the counts are impossible or `z` is not positive.
    """
    _check_counts(passes=passes, n=n)
    if passes > n:
        raise ValueError(f"passes ({passes}) cannot exceed n ({n})")
    if z <= 0:
        raise ValueError(f"z must be positive, got {z}")

    if n == 0:
        return (0.0, 1.0)

    observed = passes / n
    denominator = 1 + (z * z) / n
    centre = (observed + (z * z) / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(observed * (1 - observed) / n + (z * z) / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def _validate_thresholds(
    *, alpha: float, min_effect: float, min_samples: int, max_judge_error_rate: float
) -> None:
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie strictly between 0 and 1, got {alpha}")
    if not 0.0 <= min_effect < 1.0:
        raise ValueError(f"min_effect must lie in [0, 1), got {min_effect}")
    if not isinstance(min_samples, int) or isinstance(min_samples, bool) or min_samples < 1:
        raise ValueError(f"min_samples must be a positive integer, got {min_samples!r}")
    if not 0.0 <= max_judge_error_rate <= 1.0:
        raise ValueError(f"max_judge_error_rate must lie in [0, 1], got {max_judge_error_rate}")


def _match(
    baseline: Baseline, candidate: Baseline
) -> tuple[list[CriterionComparison], list[UnmatchedCriterion]]:
    """Pair criteria by case, position and text; report the leftovers on both sides.

    The text is part of the identity on purpose. An edited criterion is a
    different question, and answers to a new question compared against answers to
    an old one would look valid and mean nothing.
    """
    left, right = baseline.by_key(), candidate.by_key()

    rows = [
        CriterionComparison(
            case_id=key[0],
            criterion_index=key[1],
            criterion=key[2],
            baseline_passes=left[key].passes,
            baseline_n=left[key].n,
            candidate_passes=right[key].passes,
            candidate_n=right[key].n,
            hard_regression=(
                left[key].n >= HARD_REGRESSION_MIN_N
                and left[key].passes == left[key].n
                and right[key].n >= HARD_REGRESSION_MIN_N
                and right[key].passes == 0
            ),
        )
        for key in sorted(set(left) & set(right), key=lambda key: (key[0], key[1]))
    ]

    unmatched = [
        UnmatchedCriterion(
            case_id=key[0], criterion_index=key[1], criterion=key[2], present_in=side
        )
        for side, keys in (
            ("baseline", set(left) - set(right)),
            ("candidate", set(right) - set(left)),
        )
        for key in keys
    ]
    unmatched.sort(key=lambda item: (item.case_id, item.criterion_index, item.present_in))
    return rows, unmatched


def _cases(rows: list[CriterionComparison]) -> tuple[CaseComparison, ...]:
    """Pool the matched criteria of each case, in the order the cases first appear."""
    totals: dict[str, list[int]] = {}
    for row in rows:
        tally = totals.setdefault(row.case_id, [0, 0, 0, 0])
        tally[0] += row.baseline_passes
        tally[1] += row.baseline_n
        tally[2] += row.candidate_passes
        tally[3] += row.candidate_n

    return tuple(
        CaseComparison(
            case_id=case_id,
            baseline_passes=tally[0],
            baseline_n=tally[1],
            candidate_passes=tally[2],
            candidate_n=tally[3],
        )
        for case_id, tally in totals.items()
    )


def _decide(result: ComparisonResult) -> Verdict:
    """Apply the decision rule. The order of the branches is deliberate.

    A proven regression is reported even when the sample is thin or the judge was
    flaky: the finding is that the evidence exists. INCONCLUSIVE is reserved for
    the case where it does not — too few judged criteria to see a drop worth
    failing a build over, or so many failed judge calls that the candidate's pass
    rate is a rumour rather than a measurement.
    """
    if result.hard_regressions or result.statistically_significant:
        return Verdict.REGRESSION
    if (
        result.baseline_n == 0
        or result.candidate_n < result.min_samples
        or result.candidate_judge_error_rate > result.max_judge_error_rate
    ):
        return Verdict.INCONCLUSIVE
    return Verdict.NO_REGRESSION


def compare(
    baseline: Baseline,
    candidate: Baseline,
    *,
    alpha: float = DEFAULT_ALPHA,
    min_effect: float = DEFAULT_MIN_EFFECT,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    max_judge_error_rate: float = DEFAULT_MAX_JUDGE_ERROR_RATE,
) -> ComparisonResult:
    """Compare a candidate baseline against a reference baseline.

    Only criteria present on both sides are compared, and the pooled rates are
    pooled over exactly those. A criterion the goldens gained or lost cannot be a
    regression, and letting one move the headline rate would make every dataset
    edit look like a quality change; the leftovers are reported in `unmatched`
    instead of being quietly discarded.

    The judge-error rate is taken over the candidate's whole run, matched or not,
    because it measures the health of the judge rather than the quality of the
    target.

    Args:
        baseline: The reference, normally the committed baseline from `main`.
        candidate: The run under test, built with `build_baseline([run_dir])`.
        alpha: Significance level for the one-sided Fisher exact test.
        min_effect: Smallest pass-rate drop worth failing a build over.
        min_samples: Fewest matched candidate criteria for a decisive verdict.
        max_judge_error_rate: Largest share of failed candidate judge calls
            before the run is treated as unreadable.

    Raises:
        ValueError: if a threshold lies outside its range.
    """
    _validate_thresholds(
        alpha=alpha,
        min_effect=min_effect,
        min_samples=min_samples,
        max_judge_error_rate=max_judge_error_rate,
    )

    rows, unmatched = _match(baseline, candidate)
    baseline_passes = sum(row.baseline_passes for row in rows)
    baseline_n = sum(row.baseline_n for row in rows)
    candidate_passes = sum(row.candidate_passes for row in rows)
    candidate_n = sum(row.candidate_n for row in rows)

    undecided = ComparisonResult(
        verdict=Verdict.INCONCLUSIVE,
        baseline_passes=baseline_passes,
        baseline_n=baseline_n,
        baseline_interval=wilson_interval(baseline_passes, baseline_n),
        candidate_passes=candidate_passes,
        candidate_n=candidate_n,
        candidate_interval=wilson_interval(candidate_passes, candidate_n),
        p_value=fisher_exact_one_sided(
            baseline_passes, baseline_n, candidate_passes, candidate_n
        ),
        alpha=alpha,
        min_effect=min_effect,
        min_samples=min_samples,
        max_judge_error_rate=max_judge_error_rate,
        candidate_judge_errors=candidate.judge_errors,
        candidate_judge_rows=candidate.total_n + candidate.judge_errors,
        criteria=tuple(rows),
        cases=_cases(rows),
        unmatched=tuple(unmatched),
    )
    return replace(undecided, verdict=_decide(undecided))
