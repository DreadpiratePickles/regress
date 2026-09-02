"""Tests for stage 04's report: the Markdown a pull request comment carries.

`render_report` is a pure function of a `ReportData`, so almost everything here
is built from synthetic inputs rather than from a run on disk. The reader and
the CLI are exercised against a run directory built in `tmp_path`. Nothing here
calls a model or touches the network.
"""


import pytest

from regression_detect.report import (
    CriterionEvidence,
    Provenance,
    ReportData,
    render_report,
)

# --------------------------------------------------------------------------
# synthetic inputs
# --------------------------------------------------------------------------


def criterion_row(
    case_id: str,
    index: int,
    *,
    text: str,
    baseline: tuple[int, int],
    candidate: tuple[int, int],
    hard: bool = False,
) -> dict:
    baseline_rate = baseline[0] / baseline[1] if baseline[1] else None
    candidate_rate = candidate[0] / candidate[1] if candidate[1] else None
    difference = (
        None
        if baseline_rate is None or candidate_rate is None
        else candidate_rate - baseline_rate
    )
    return {
        "case_id": case_id,
        "criterion_index": index,
        "criterion": text,
        "baseline": {"passes": baseline[0], "n": baseline[1]},
        "candidate": {"passes": candidate[0], "n": candidate[1]},
        "difference": difference,
        "hard_regression": hard,
    }


def comparison_payload(
    *,
    verdict: str = "REGRESSION",
    explanation: str = "Pass rate fell from 100.0% to 60.0% → REGRESSION.",
    criteria: list[dict] | None = None,
    unmatched: list[dict] | None = None,
    judge_errors: tuple[int, int] = (0, 5),
    identity: dict | None = None,
) -> dict:
    rows = criteria if criteria is not None else [
        criterion_row("refund", 0, text="States the amount.", baseline=(2, 2), candidate=(0, 2),
                      hard=True),
        criterion_row("refund", 1, text="Names the issue.", baseline=(2, 2), candidate=(1, 2)),
        criterion_row("delay", 0, text="Mentions the delay.", baseline=(1, 2), candidate=(2, 2)),
        criterion_row("delay", 1, text="Stays to 3 sentences.", baseline=(2, 2),
                      candidate=(2, 2)),
    ]
    errors, error_rows = judge_errors
    return {
        "schema_version": 1,
        "verdict": verdict,
        "explanation": explanation,
        "overall": {
            "baseline": {"passes": 7, "n": 8, "rate": 0.875, "wilson_95": [0.5288, 0.9779]},
            "candidate": {"passes": 5, "n": 8, "rate": 0.625, "wilson_95": [0.3079, 0.8614]},
            "difference": -0.25,
        },
        "p_value": 0.0044,
        "thresholds": {
            "alpha": 0.05,
            "min_effect": 0.05,
            "min_samples": 30,
            "max_judge_error_rate": 0.2,
        },
        "candidate_judge_errors": {
            "errors": errors,
            "rows": error_rows,
            "rate": errors / error_rows if error_rows else 0.0,
        },
        "cases": [],
        "criteria": rows,
        "unmatched": unmatched or [],
        "identity": identity if identity is not None else {
            "checked": True,
            "goldens_sha256": {"baseline": "c" * 64, "candidate": "c" * 64},
            "target_model_id": {"baseline": "target-model", "candidate": "target-model"},
            "judge_model_id": {"baseline": "judge-model", "candidate": "judge-model"},
            "judge_prompt_sha256": {"baseline": "b" * 64, "candidate": "b" * 64},
            "target_prompt_sha256": {"baseline": "d" * 64, "candidate": "a" * 64},
        },
        "baseline_source": {
            "created_at_utc": "2026-01-01T00:00:00Z",
            "run_ids": ["2026-01-01T00-00-00Z", "2026-01-01T01-00-00Z"],
        },
    }


def provenance() -> Provenance:
    return Provenance(
        run_id="2026-09-02T10-00-00Z",
        target_model_id="target-model",
        judge_model_id="judge-model",
        prompt_sha256="a" * 64,
        judge_prompt_sha256="b" * 64,
        goldens_sha256="c" * 64,
        samples=1,
        baseline_run_ids=("2026-01-01T00-00-00Z", "2026-01-01T01-00-00Z"),
    )


def report_data(**overrides) -> ReportData:
    defaults = {
        "comparison": comparison_payload(),
        "provenance": provenance(),
        "scores_overall": {
            "criteria_total": 8,
            "passed": 5,
            "failed": 3,
            "errored": 0,
            "pass_rate": 0.625,
        },
        "evidence": (
            CriterionEvidence(
                case_id="refund",
                criterion_index=0,
                criterion="States the amount.",
                outputs=("The customer wants help with an order.",),
                reasons=("The summary never states the amount.",),
            ),
        ),
    }
    return ReportData(**{**defaults, **overrides})


# --------------------------------------------------------------------------
# the badge, the title and the explanation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "badge"),
    [
        ("REGRESSION", "🔴 REGRESSION"),
        ("NO_REGRESSION", "🟢 NO_REGRESSION"),
        ("INCONCLUSIVE", "🟡 INCONCLUSIVE"),
    ],
)
def test_the_badge_line_matches_the_verdict(verdict: str, badge: str) -> None:
    data = report_data(comparison=comparison_payload(verdict=verdict))

    assert badge in render_report(data)


def test_the_title_is_an_h2_naming_the_run() -> None:
    rendered = render_report(report_data())

    assert rendered.startswith("## ")
    assert "2026-09-02T10-00-00Z" in rendered.splitlines()[0]


def test_the_explanation_sentence_is_quoted_verbatim() -> None:
    sentence = "Pass rate fell from 100.0% to 60.0%; one criterion → REGRESSION."
    data = report_data(comparison=comparison_payload(explanation=sentence))

    assert sentence in render_report(data)


# --------------------------------------------------------------------------
# the overall table
# --------------------------------------------------------------------------


def test_the_overall_table_carries_both_rates_their_intervals_and_the_thresholds() -> None:
    rendered = render_report(report_data())

    assert "7 / 8" in rendered
    assert "5 / 8" in rendered
    assert "87.5%" in rendered
    assert "62.5%" in rendered
    assert "52.9%–97.8%" in rendered
    assert "30.8%–86.1%" in rendered
    assert "0.0044" in rendered
    assert "5 points" in rendered
    assert "0.05" in rendered


def test_the_candidate_run_tally_comes_from_the_scores_file() -> None:
    data = report_data(
        scores_overall={
            "criteria_total": 9,
            "passed": 5,
            "failed": 3,
            "errored": 1,
            "pass_rate": 0.625,
        }
    )

    rendered = render_report(data)

    assert "5 passed" in rendered
    assert "3 failed" in rendered
    assert "1 not judged" in rendered


def test_the_judge_error_count_is_reported() -> None:
    data = report_data(comparison=comparison_payload(judge_errors=(2, 10)))

    assert "Judge errors: 2 of 10" in render_report(data)


# --------------------------------------------------------------------------
# the two criterion tables
# --------------------------------------------------------------------------


def test_worsened_criteria_are_listed_and_improved_ones_are_kept_apart() -> None:
    rendered = render_report(report_data())

    worse = rendered.index("Criteria that got worse")
    better = rendered.index("Criteria that improved")
    assert worse < better
    assert "States the amount." in rendered[worse:better]
    assert "Mentions the delay." in rendered[better:]


def test_a_criterion_that_did_not_move_appears_in_neither_table() -> None:
    rendered = render_report(report_data())

    assert "Stays to 3 sentences." not in rendered


def test_worsened_criteria_are_sorted_by_the_size_of_the_drop() -> None:
    criteria = [
        criterion_row("a", 0, text="small drop", baseline=(4, 4), candidate=(3, 4)),
        criterion_row("b", 0, text="big drop", baseline=(4, 4), candidate=(0, 4)),
        criterion_row("c", 0, text="middle drop", baseline=(4, 4), candidate=(2, 4)),
    ]
    rendered = render_report(report_data(comparison=comparison_payload(criteria=criteria)))

    assert rendered.index("big drop") < rendered.index("middle drop") < rendered.index(
        "small drop"
    )


def test_a_hard_regression_is_marked_in_the_worsened_table() -> None:
    rendered = render_report(report_data())

    marked = [line for line in rendered.splitlines() if "States the amount." in line]
    assert marked and "‼️" in marked[0]
    assert "hard regression" in rendered


def test_the_hard_regression_note_is_omitted_when_nothing_is_marked() -> None:
    criteria = [criterion_row("a", 0, text="ordinary drop", baseline=(4, 4), candidate=(1, 4))]
    rendered = render_report(report_data(comparison=comparison_payload(criteria=criteria)))

    assert "Criteria that got worse" in rendered
    assert "hard regression" not in rendered


def test_the_worsened_table_is_replaced_by_a_sentence_when_nothing_fell() -> None:
    criteria = [criterion_row("a", 0, text="held", baseline=(4, 4), candidate=(4, 4))]
    rendered = render_report(
        report_data(comparison=comparison_payload(verdict="NO_REGRESSION", criteria=criteria))
    )

    assert "No criterion scored lower than the baseline." in rendered
    assert "Criteria that improved" not in rendered


# --------------------------------------------------------------------------
# unmatched criteria and evidence
# --------------------------------------------------------------------------


def test_unmatched_criteria_are_noted_with_the_side_they_appear_on() -> None:
    unmatched = [
        {
            "case_id": "new_case",
            "criterion_index": 0,
            "criterion": "A criterion added this week.",
            "present_in": "candidate",
        }
    ]
    rendered = render_report(report_data(comparison=comparison_payload(unmatched=unmatched)))

    assert "A criterion added this week." in rendered
    assert "candidate" in rendered
    assert "goldens changed" in rendered


def test_no_unmatched_note_when_every_criterion_matched() -> None:
    assert "Unmatched" not in render_report(report_data())


def test_each_regressed_criterion_gets_a_details_block_with_output_and_reason() -> None:
    rendered = render_report(report_data())

    assert "<details>" in rendered
    assert "<summary>" in rendered
    assert "The customer wants help with an order." in rendered
    assert "The summary never states the amount." in rendered


def test_a_regressed_criterion_without_evidence_still_renders() -> None:
    rendered = render_report(report_data(evidence=()))

    assert "REGRESSION" in rendered
    assert "<details>" not in rendered


# --------------------------------------------------------------------------
# provenance footer
# --------------------------------------------------------------------------


def test_the_footer_carries_the_provenance_with_short_hashes() -> None:
    rendered = render_report(report_data())

    assert "2026-09-02T10-00-00Z" in rendered
    assert "target-model" in rendered
    assert "judge-model" in rendered
    assert "a" * 12 in rendered
    assert "a" * 64 not in rendered
    assert "2026-01-01T01-00-00Z" in rendered
    assert "samples 1" in rendered.lower()


def test_the_footer_states_what_made_the_two_runs_comparable() -> None:
    rendered = render_report(report_data())

    line = next(line for line in rendered.splitlines() if line.startswith("Comparability:"))
    assert "target model `target-model`" in line
    assert "judge model `judge-model`" in line
    assert "judge prompt `" + "b" * 12 + "…`" in line
    assert "goldens `" + "c" * 12 + "…`" in line
    assert "b" * 64 not in line


def test_the_footer_says_when_comparability_was_not_checked() -> None:
    identity = {
        "checked": False,
        "goldens_sha256": {"baseline": "c" * 64, "candidate": "c" * 64},
        "target_model_id": {"baseline": "target-model", "candidate": "dry-run-fake"},
        "judge_model_id": {"baseline": "judge-model", "candidate": "dry-run-fake-judge"},
        "judge_prompt_sha256": {"baseline": "b" * 64, "candidate": "b" * 64},
        "target_prompt_sha256": {"baseline": "d" * 64, "candidate": "a" * 64},
    }
    rendered = render_report(report_data(comparison=comparison_payload(identity=identity)))

    line = next(line for line in rendered.splitlines() if line.startswith("Comparability:"))
    assert "not checked" in line
    assert "dry-run-fake" in line


def test_the_footer_omits_comparability_when_the_comparison_predates_it() -> None:
    payload = comparison_payload()
    del payload["identity"]

    rendered = render_report(report_data(comparison=payload))

    assert "Comparability:" not in rendered


def test_the_report_never_contains_an_absolute_path() -> None:
    rendered = render_report(report_data())

    assert "/Users/" not in rendered
    assert "/home/" not in rendered
    for line in rendered.splitlines():
        assert not line.lstrip().startswith("/")
