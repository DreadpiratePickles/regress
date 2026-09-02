"""Tests for the stage 03 statistics.

`compare.py` is pure: no file is read, no clock is consulted, no model is
called. Every expected number below is either hand-computable or produced by a
deliberately slow reference implementation written inside this file, so the
tests do not simply restate the implementation they are checking.
"""

import math
from fractions import Fraction

import pytest

from regression_detect.baseline import Baseline, CriterionStat
from regression_detect.compare import (
    DEFAULT_MAX_JUDGE_ERROR_RATE,
    DEFAULT_MIN_SAMPLES,
    Verdict,
    compare,
    fisher_exact_one_sided,
    wilson_interval,
)


def reference_fisher(a_pass: int, a_n: int, b_pass: int, b_n: int) -> float:
    """Slow, exact reference: sum the hypergeometric tail in rationals.

    Rows are baseline and candidate, columns are pass and fail. Conditioning on
    both margins, the candidate's pass count is hypergeometric; the one-sided
    p-value for "the candidate is worse" is the lower tail up to what was seen.
    Written independently of `compare.py` and evaluated with `Fraction`, so a
    floating-point shortcut in the implementation cannot hide here.
    """
    total = a_n + b_n
    passes = a_pass + b_pass
    lowest = max(0, b_n - (total - passes))
    tail = Fraction(0)
    for observed in range(lowest, b_pass + 1):
        tail += Fraction(
            math.comb(passes, observed) * math.comb(total - passes, b_n - observed),
            math.comb(total, b_n),
        )
    return float(tail)


def stat(case_id: str, index: int, passes: int, n: int, criterion: str | None = None):
    return CriterionStat(
        case_id=case_id,
        criterion_index=index,
        criterion=criterion or f"{case_id} criterion {index}",
        n=n,
        passes=passes,
        judge_errors=0,
    )


def make_baseline(
    stats: list[CriterionStat],
    *,
    judge_errors: int = 0,
    run_ids: tuple[str, ...] = ("run-a",),
    target_model_id: str = "test-target",
    judge_model_id: str = "test-judge",
) -> Baseline:
    """A `Baseline` built directly, without touching the filesystem."""
    return Baseline(
        schema_version=1,
        created_at_utc="2026-01-01T00:00:00Z",
        run_ids=run_ids,
        goldens_sha256="a" * 64,
        prompt_sha256="b" * 64,
        target_model_id=target_model_id,
        judge_prompt_sha256="c" * 64,
        judge_model_id=judge_model_id,
        criteria=tuple(stats),
        total_n=sum(item.n for item in stats),
        total_passes=sum(item.passes for item in stats),
        judge_errors=judge_errors,
    )


def spread(passes: int, count: int) -> list[CriterionStat]:
    """`count` single-sample criteria of which the first `passes` passed."""
    return [stat("c", index, 1 if index < passes else 0, 1) for index in range(count)]


# --------------------------------------------------------------------------
# fisher_exact_one_sided
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        (65, 67, 55, 67),
        (67, 67, 60, 67),
        (10, 10, 5, 10),
        (5, 10, 5, 10),
        (2, 2, 0, 2),
        (1, 2, 1, 2),
        (9, 10, 10, 10),
        (0, 10, 0, 10),
        (3, 7, 6, 11),
        (30, 40, 12, 40),
    ],
)
def test_fisher_matches_the_slow_reference(table):
    assert fisher_exact_one_sided(*table) == pytest.approx(reference_fisher(*table), abs=1e-12)


def test_fisher_on_the_headline_table():
    """65/67 against 55/67 — the table the report's worked example uses."""
    assert fisher_exact_one_sided(65, 67, 55, 67) == pytest.approx(0.0044060294, abs=1e-9)


def test_fisher_hand_computed_small_table():
    """2/2 against 0/2: 4 items, 2 passes, both must land in the baseline row.

    C(2,0)·C(2,2)/C(4,2) = 1/6.
    """
    assert fisher_exact_one_sided(2, 2, 0, 2) == pytest.approx(1 / 6)


def test_fisher_is_one_when_the_candidate_swept_every_criterion():
    """The whole lower tail is included, so the p-value is exactly 1."""
    assert fisher_exact_one_sided(9, 10, 10, 10) == pytest.approx(1.0)


def test_fisher_on_identical_tables_is_far_from_significant():
    assert fisher_exact_one_sided(5, 10, 5, 10) > 0.5


def test_fisher_shrinks_as_the_drop_grows():
    p_values = [fisher_exact_one_sided(65, 67, passes, 67) for passes in (64, 60, 55, 50)]
    assert p_values == sorted(p_values, reverse=True)


def test_fisher_with_no_variation_anywhere_is_one():
    assert fisher_exact_one_sided(0, 10, 0, 10) == pytest.approx(1.0)
    assert fisher_exact_one_sided(10, 10, 10, 10) == pytest.approx(1.0)


@pytest.mark.parametrize("table", [(0, 0, 5, 10), (5, 10, 0, 0), (0, 0, 0, 0)])
def test_fisher_with_an_empty_side_is_one(table):
    """No evidence is not evidence of a regression."""
    assert fisher_exact_one_sided(*table) == 1.0


@pytest.mark.parametrize(
    "table",
    [(-1, 10, 5, 10), (5, 10, -1, 10), (5, 10, 5, -10), (11, 10, 5, 10), (5, 10, 11, 10)],
)
def test_fisher_rejects_an_impossible_table(table):
    with pytest.raises(ValueError):
        fisher_exact_one_sided(*table)


def test_fisher_stays_within_zero_and_one():
    for baseline_passes in range(0, 9):
        for candidate_passes in range(0, 9):
            value = fisher_exact_one_sided(baseline_passes, 8, candidate_passes, 8)
            assert 0.0 <= value <= 1.0


# --------------------------------------------------------------------------
# wilson_interval
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("passes", "n", "low", "high"),
    [
        (0, 10, 0.0, 0.277540169),
        (10, 10, 0.722459831, 1.0),
        (5, 10, 0.236589594, 0.763410406),
        (50, 100, 0.403829829, 0.596170171),
        (65, 67, 0.897532500, 0.991775478),
        (55, 67, 0.712522864, 0.894465121),
    ],
)
def test_wilson_known_values(passes, n, low, high):
    got_low, got_high = wilson_interval(passes, n)
    assert got_low == pytest.approx(low, abs=1e-9)
    assert got_high == pytest.approx(high, abs=1e-9)


def test_wilson_with_no_samples_is_the_whole_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_brackets_the_observed_rate():
    low, high = wilson_interval(55, 67)
    assert low < 55 / 67 < high


def test_wilson_narrows_as_n_grows():
    small = wilson_interval(5, 10)
    large = wilson_interval(500, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_is_clamped_to_zero_and_one():
    low, high = wilson_interval(1, 1)
    assert low >= 0.0
    assert high == 1.0


@pytest.mark.parametrize("bad", [(-1, 10), (11, 10), (5, -1)])
def test_wilson_rejects_impossible_counts(bad):
    with pytest.raises(ValueError):
        wilson_interval(*bad)


def test_wilson_rejects_a_non_positive_z():
    with pytest.raises(ValueError):
        wilson_interval(5, 10, z=0.0)


# --------------------------------------------------------------------------
# compare — overall numbers
# --------------------------------------------------------------------------


def test_compare_reports_pooled_rates_and_intervals():
    result = compare(
        make_baseline(spread(65, 67)),
        make_baseline(spread(55, 67)),
        alpha=0.05,
        min_effect=0.05,
    )

    assert (result.baseline_passes, result.baseline_n) == (65, 67)
    assert (result.candidate_passes, result.candidate_n) == (55, 67)
    assert result.baseline_rate == pytest.approx(65 / 67)
    assert result.candidate_rate == pytest.approx(55 / 67)
    assert result.difference == pytest.approx(55 / 67 - 65 / 67)
    assert result.drop == pytest.approx(10 / 67)
    assert result.p_value == pytest.approx(0.0044060294, abs=1e-9)
    assert result.baseline_interval[0] == pytest.approx(0.897532500, abs=1e-9)
    assert result.candidate_interval[1] == pytest.approx(0.894465121, abs=1e-9)


def test_compare_records_the_thresholds_it_used():
    result = compare(
        make_baseline(spread(10, 10)),
        make_baseline(spread(10, 10)),
        alpha=0.01,
        min_effect=0.10,
        min_samples=5,
        max_judge_error_rate=0.5,
    )
    assert (result.alpha, result.min_effect) == (0.01, 0.10)
    assert (result.min_samples, result.max_judge_error_rate) == (5, 0.5)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"alpha": 0.0}, "alpha"),
        ({"alpha": 1.0}, "alpha"),
        ({"min_effect": -0.1}, "min_effect"),
        ({"min_effect": 1.5}, "min_effect"),
        ({"min_samples": 0}, "min_samples"),
        ({"max_judge_error_rate": 1.5}, "max_judge_error_rate"),
    ],
)
def test_compare_rejects_impossible_thresholds(kwargs, message):
    settings = {"alpha": 0.05, "min_effect": 0.05, **kwargs}
    with pytest.raises(ValueError, match=message):
        compare(make_baseline(spread(10, 10)), make_baseline(spread(10, 10)), **settings)


# --------------------------------------------------------------------------
# compare — the verdict decision matrix
# --------------------------------------------------------------------------


def test_verdict_regression_when_the_drop_is_large_and_significant():
    result = compare(
        make_baseline(spread(65, 67)),
        make_baseline(spread(55, 67)),
        alpha=0.05,
        min_effect=0.05,
    )
    assert result.verdict is Verdict.REGRESSION


def test_verdict_no_regression_when_the_drop_is_significant_but_small():
    """A real but tiny drop is not worth failing a build over."""
    baseline = make_baseline([stat("c", index, 1000, 1000) for index in range(1)])
    candidate = make_baseline([stat("c", 0, 970, 1000)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05)

    assert result.p_value < 0.05
    assert result.drop == pytest.approx(0.03)
    assert result.verdict is Verdict.NO_REGRESSION


def test_verdict_no_regression_when_the_drop_is_large_but_noisy():
    """A 20-point drop over 40 criteria clears min_effect but not alpha."""
    result = compare(
        make_baseline(spread(34, 40)),
        make_baseline(spread(30, 40)),
        alpha=0.01,
        min_effect=0.05,
    )
    assert result.drop >= 0.05
    assert result.p_value >= 0.01
    assert result.verdict is Verdict.NO_REGRESSION


def test_verdict_no_regression_when_the_rate_improved():
    result = compare(
        make_baseline(spread(40, 67)),
        make_baseline(spread(60, 67)),
        alpha=0.05,
        min_effect=0.05,
    )
    assert result.difference > 0
    assert result.verdict is Verdict.NO_REGRESSION


def test_verdict_regression_on_a_hard_regression_alone():
    """One criterion that always passed and now always fails is enough."""
    baseline = make_baseline([stat("c", 0, 2, 2), *[stat("c", i, 2, 2) for i in range(1, 40)]])
    candidate = make_baseline([stat("c", 0, 0, 2), *[stat("c", i, 2, 2) for i in range(1, 40)]])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05)

    assert result.drop < 0.05
    assert [item.criterion_index for item in result.hard_regressions] == [0]
    assert result.verdict is Verdict.REGRESSION


@pytest.mark.parametrize(
    ("baseline_stat", "candidate_stat"),
    [
        ((2, 2), (0, 1)),  # candidate n below the floor
        ((1, 1), (0, 2)),  # baseline n below the floor
        ((1, 2), (0, 2)),  # baseline did not always pass
        ((2, 2), (1, 2)),  # candidate did not always fail
    ],
)
def test_hard_regression_needs_all_pass_then_all_fail_with_n_at_least_two(
    baseline_stat, candidate_stat
):
    baseline = make_baseline([stat("c", 0, *baseline_stat)])
    candidate = make_baseline([stat("c", 0, *candidate_stat)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    assert result.hard_regressions == ()
    assert result.criteria[0].hard_regression is False


def test_verdict_inconclusive_when_the_candidate_judged_too_few_criteria():
    """A drop that clears min_effect but not alpha, over too small a sample."""
    result = compare(
        make_baseline(spread(20, 20)),
        make_baseline(spread(19, 20)),
        alpha=0.05,
        min_effect=0.05,
        min_samples=DEFAULT_MIN_SAMPLES,
    )
    assert result.candidate_n < DEFAULT_MIN_SAMPLES
    assert result.verdict is Verdict.INCONCLUSIVE


def test_verdict_inconclusive_when_the_judge_failed_on_too_many_rows():
    baseline = make_baseline(spread(40, 40))
    candidate = make_baseline(spread(39, 40), judge_errors=20)
    result = compare(candidate=candidate, baseline=baseline, alpha=0.05, min_effect=0.05)

    assert result.candidate_judge_error_rate == pytest.approx(20 / 60)
    assert result.candidate_judge_error_rate > DEFAULT_MAX_JUDGE_ERROR_RATE
    assert result.verdict is Verdict.INCONCLUSIVE


def test_judge_errors_exactly_at_the_ceiling_are_not_inconclusive():
    """The rule is 'more than' the ceiling, not 'at least'."""
    baseline = make_baseline(spread(40, 40))
    candidate = make_baseline(spread(39, 40), judge_errors=10)
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05)

    assert result.candidate_judge_error_rate == pytest.approx(0.2)
    assert result.verdict is Verdict.NO_REGRESSION


def test_verdict_inconclusive_when_nothing_matched():
    baseline = make_baseline([stat("only_baseline", 0, 1, 1)])
    candidate = make_baseline([stat("only_candidate", 0, 0, 1)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05)

    assert (result.baseline_n, result.candidate_n) == (0, 0)
    assert result.verdict is Verdict.INCONCLUSIVE


def test_a_proven_regression_outranks_a_thin_sample():
    """Evidence that exists is reported; INCONCLUSIVE means evidence is absent."""
    baseline = make_baseline([stat("c", index, 2, 2) for index in range(4)])
    candidate = make_baseline([stat("c", index, 0, 2) for index in range(4)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=100)

    assert result.candidate_n < 100
    assert result.verdict is Verdict.REGRESSION


# --------------------------------------------------------------------------
# compare — per-criterion, per-case, unmatched
# --------------------------------------------------------------------------


def test_per_criterion_rows_carry_both_sides():
    baseline = make_baseline([stat("alpha", 0, 2, 2), stat("beta", 0, 1, 2)])
    candidate = make_baseline([stat("alpha", 0, 1, 2), stat("beta", 0, 2, 2)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    rows = {(row.case_id, row.criterion_index): row for row in result.criteria}
    assert rows[("alpha", 0)].baseline_passes == 2
    assert rows[("alpha", 0)].candidate_passes == 1
    assert rows[("alpha", 0)].difference == pytest.approx(-0.5)
    assert rows[("beta", 0)].difference == pytest.approx(0.5)


def test_criteria_rows_are_ordered_by_case_then_index():
    baseline = make_baseline([stat("b", 1, 1, 1), stat("a", 0, 1, 1), stat("a", 1, 1, 1)])
    candidate = make_baseline([stat("a", 1, 1, 1), stat("b", 1, 1, 1), stat("a", 0, 1, 1)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    assert [(row.case_id, row.criterion_index) for row in result.criteria] == [
        ("a", 0),
        ("a", 1),
        ("b", 1),
    ]


def test_per_case_summary_pools_that_case_only():
    baseline = make_baseline(
        [stat("alpha", 0, 2, 2), stat("alpha", 1, 2, 2), stat("beta", 0, 2, 2)]
    )
    candidate = make_baseline(
        [stat("alpha", 0, 0, 2), stat("alpha", 1, 1, 2), stat("beta", 0, 2, 2)]
    )
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    cases = {case.case_id: case for case in result.cases}
    assert (cases["alpha"].baseline_passes, cases["alpha"].baseline_n) == (4, 4)
    assert (cases["alpha"].candidate_passes, cases["alpha"].candidate_n) == (1, 4)
    assert cases["alpha"].difference == pytest.approx(-0.75)
    assert cases["beta"].difference == pytest.approx(0.0)


def test_criteria_present_on_one_side_only_are_reported_not_dropped():
    baseline = make_baseline([stat("shared", 0, 1, 1), stat("removed", 0, 1, 1)])
    candidate = make_baseline([stat("shared", 0, 1, 1), stat("added", 0, 0, 1)])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    assert [(item.case_id, item.present_in) for item in result.unmatched] == [
        ("added", "candidate"),
        ("removed", "baseline"),
    ]
    assert [row.case_id for row in result.criteria] == ["shared"]
    assert (result.baseline_n, result.candidate_n) == (1, 1)


def test_an_edited_criterion_is_unmatched_on_both_sides():
    """Position is not identity: comparing reworded criteria compares nothing."""
    baseline = make_baseline([stat("c", 0, 1, 1, criterion="Names the order number")])
    candidate = make_baseline([stat("c", 0, 0, 1, criterion="Names the order id")])
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    assert result.criteria == ()
    assert sorted(item.present_in for item in result.unmatched) == ["baseline", "candidate"]


def test_flagged_rows_are_the_ones_worth_printing():
    baseline = make_baseline(
        [stat("a", 0, 2, 2), stat("a", 1, 2, 2), stat("b", 0, 1, 2)]
    )
    candidate = make_baseline(
        [stat("a", 0, 0, 2), stat("a", 1, 2, 2), stat("b", 0, 2, 2)]
    )
    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05, min_samples=1)

    assert [(row.case_id, row.criterion_index) for row in result.flagged] == [("a", 0)]


# --------------------------------------------------------------------------
# compare — explain() and to_json()
# --------------------------------------------------------------------------


def test_explain_carries_every_number_the_verdict_rests_on():
    result = compare(
        make_baseline(spread(65, 67)),
        make_baseline(spread(55, 67)),
        alpha=0.05,
        min_effect=0.05,
    )
    text = result.explain()

    for fragment in ("97.0%", "65/67", "82.1%", "55/67", "14.9 points", "0.004", "0.05"):
        assert fragment in text, f"{fragment!r} missing from: {text}"
    assert text.rstrip().endswith("REGRESSION.")


def test_explain_names_the_hard_regression_rule():
    baseline = make_baseline([stat("c", index, 2, 2) for index in range(40)])
    candidate = make_baseline(
        [stat("c", 0, 0, 2), *[stat("c", index, 2, 2) for index in range(1, 40)]]
    )
    text = compare(baseline, candidate, alpha=0.05, min_effect=0.05).explain()

    assert "always passed" in text
    assert "REGRESSION." in text


def test_explain_names_the_minimum_effect_when_the_drop_is_too_small():
    baseline = make_baseline([stat("c", 0, 1000, 1000)])
    candidate = make_baseline([stat("c", 0, 970, 1000)])
    text = compare(baseline, candidate, alpha=0.05, min_effect=0.05).explain()

    assert "minimum effect" in text
    assert text.rstrip().endswith("NO_REGRESSION.")


def test_explain_names_the_p_value_when_the_drop_is_not_significant():
    text = compare(
        make_baseline(spread(34, 40)),
        make_baseline(spread(30, 40)),
        alpha=0.01,
        min_effect=0.05,
    ).explain()

    assert "not below 0.01" in text
    assert text.rstrip().endswith("NO_REGRESSION.")


def test_explain_says_when_the_rate_did_not_fall():
    text = compare(
        make_baseline(spread(40, 67)),
        make_baseline(spread(60, 67)),
        alpha=0.05,
        min_effect=0.05,
    ).explain()

    assert "did not fall" in text
    assert text.rstrip().endswith("NO_REGRESSION.")


def test_explain_names_the_sample_floor_when_inconclusive():
    text = compare(
        make_baseline(spread(20, 20)),
        make_baseline(spread(19, 20)),
        alpha=0.05,
        min_effect=0.05,
        min_samples=30,
    ).explain()

    assert "20" in text and "30" in text
    assert text.rstrip().endswith("INCONCLUSIVE.")


def test_explain_names_the_judge_error_ceiling_when_inconclusive():
    text = compare(
        make_baseline(spread(40, 40)),
        make_baseline(spread(39, 40), judge_errors=20),
        alpha=0.05,
        min_effect=0.05,
    ).explain()

    assert "judge" in text
    assert "20 of 60" in text
    assert text.rstrip().endswith("INCONCLUSIVE.")


def test_explain_says_nothing_matched_only_when_nothing_matched():
    baseline = make_baseline([stat("only_baseline", 0, 1, 1)])
    candidate = make_baseline([stat("only_candidate", 0, 0, 1)])

    text = compare(baseline, candidate, alpha=0.05, min_effect=0.05).explain()

    assert "no criterion matched" in text
    assert "INCONCLUSIVE." in text


def test_explain_distinguishes_matched_but_wholly_unjudged_criteria():
    """Criteria matched and were judged zero times: the judge failed, not the match."""
    baseline = make_baseline([stat("c", index, 0, 0) for index in range(3)])
    candidate = make_baseline([stat("c", index, 0, 0) for index in range(3)])

    result = compare(baseline, candidate, alpha=0.05, min_effect=0.05)
    text = result.explain()

    assert len(result.criteria) == 3
    assert (result.baseline_n, result.candidate_n) == (0, 0)
    assert result.verdict is Verdict.INCONCLUSIVE
    assert "no criterion matched" not in text
    assert "3 criteria matched" in text
    assert "judge call" in text
    assert text.rstrip().endswith("INCONCLUSIVE.")


def test_explain_names_the_side_that_judged_nothing():
    baseline = make_baseline([stat("c", index, 1, 1) for index in range(3)])
    candidate = make_baseline([stat("c", index, 0, 0) for index in range(3)])

    text = compare(baseline, candidate, alpha=0.05, min_effect=0.05).explain()

    assert "the candidate judged none of them" in text


def test_explain_mentions_unmatched_criteria():
    baseline = make_baseline([*spread(40, 40), stat("removed", 0, 1, 1)])
    candidate = make_baseline([*spread(40, 40), stat("added", 0, 1, 1)])
    text = compare(baseline, candidate, alpha=0.05, min_effect=0.05).explain()

    assert "goldens changed" in text
    assert "2 criteria" in text


def test_to_json_round_trips_the_numbers_the_report_needs():
    result = compare(
        make_baseline(spread(65, 67)),
        make_baseline(spread(55, 67)),
        alpha=0.05,
        min_effect=0.05,
    )
    payload = result.to_json()

    assert payload["verdict"] == "REGRESSION"
    assert payload["schema_version"] == 1
    assert payload["overall"]["baseline"]["passes"] == 65
    assert payload["overall"]["candidate"]["n"] == 67
    assert payload["p_value"] == pytest.approx(0.0044060294, abs=1e-9)
    assert payload["explanation"] == result.explain()
    assert len(payload["criteria"]) == 67
    assert payload["thresholds"]["alpha"] == 0.05


def test_explain_reads_naturally_when_the_rate_held():
    text = compare(
        make_baseline(spread(40, 40)),
        make_baseline(spread(40, 40)),
        alpha=0.05,
        min_effect=0.05,
    ).explain()

    assert "Pass rate held at 100.0% (40/40 baseline, 40/40 candidate)" in text
    assert text.rstrip().endswith("NO_REGRESSION.")
