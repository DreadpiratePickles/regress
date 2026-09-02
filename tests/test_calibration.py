"""Tests for judge calibration against human labels.

The human labels are the ticks in `review.md`; the judge labels are the verdicts
in `verdicts.jsonl`. Both are parsed deterministically and all arithmetic is
asserted against hand-computed numbers.
"""

import json
from pathlib import Path

import pytest

from regression_detect.calibration import (
    CALIBRATION_FILENAME,
    CalibrationError,
    calibrate,
    main,
    parse_graded_cases,
    parse_review_checkboxes,
    render_table,
)
from regression_detect.providers.fake import FakeProvider
from regression_detect.runner import run_goldens

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GOLDENS = REPO_ROOT / "goldens" / "cases.yaml"

REVIEW_MD = """\
# Golden run review — stage 01

- Run: `2026-01-01T00-00-00Z`
- Calls: 3 total, 3 ok, 0 failed

---
## alpha

### Input

```text
a ticket
```

### Output

```text
a summary that happens to contain
- [x] a line that looks like a ticked criterion
```

### Criteria

- [x] Alpha criterion one.
- [ ] Alpha criterion two.
- [X] Alpha criterion three.

---
## beta

### Input

```text
another ticket
```

### Output

```text
another summary
```

### Criteria

- [ ] Beta criterion one.
- [x] Beta criterion two.

---
## gamma

### Input

```text
a third ticket
```

### Output

```text
a third summary
```

### Criteria

- [x] Gamma criterion one.
- [x] Gamma criterion two.
"""


def verdict_row(
    case_id: str,
    criterion_index: int,
    passed: bool | None,
    *,
    reason: str | None = "because.",
    sample_index: int = 0,
    judge_sample_index: int = 0,
) -> dict:
    return {
        "case_id": case_id,
        "sample_index": sample_index,
        "criterion_index": criterion_index,
        "criterion": f"{case_id} criterion {criterion_index}",
        "judge_sample_index": judge_sample_index,
        "passed": passed,
        "reason": reason,
        "judge_model_id": "fake-provider",
        "judge_prompt_sha256": "0" * 64,
        "latency_ms": 1,
        "error_type": None if passed is not None else "JudgeParseError",
        "error": None if passed is not None else "unparseable",
    }


HAND_BUILT_VERDICTS = [
    # alpha: human x / _ / x   judge True / True / error
    verdict_row("alpha", 0, True),
    verdict_row("alpha", 1, True),
    verdict_row("alpha", 2, None, reason=None),
    # beta: human _ / x        judge False / False
    verdict_row("beta", 0, False),
    verdict_row("beta", 1, False),
    # gamma is not graded and must be ignored entirely
    verdict_row("gamma", 0, True),
    verdict_row("gamma", 1, False),
    # distractors: only sample 0 / judge sample 0 may be compared
    verdict_row("alpha", 0, False, judge_sample_index=1),
    verdict_row("alpha", 0, False, sample_index=1),
]


def build_run_dir(tmp_path: Path, *, review: str = REVIEW_MD, verdicts=None) -> Path:
    run_dir = tmp_path / "runs" / "2026-01-01T00-00-00Z"
    run_dir.mkdir(parents=True)
    (run_dir / "review.md").write_text(review, encoding="utf-8")
    rows = HAND_BUILT_VERDICTS if verdicts is None else verdicts
    (run_dir / "verdicts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return run_dir


# --- parsing review.md ------------------------------------------------------


def test_parse_review_reads_ticks_in_criterion_order() -> None:
    ticks = parse_review_checkboxes(REVIEW_MD)

    assert ticks["alpha"] == [True, False, True]
    assert ticks["beta"] == [False, True]
    assert ticks["gamma"] == [True, True]


def test_parse_review_ignores_checkbox_lines_inside_output_blocks() -> None:
    ticks = parse_review_checkboxes(REVIEW_MD)

    assert len(ticks["alpha"]) == 3


def test_parse_review_of_a_real_stage_01_review(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=FakeProvider("A short summary."),
    )

    ticks = parse_review_checkboxes((summary.out_dir / "review.md").read_text(encoding="utf-8"))

    assert len(ticks) == 15
    assert ticks["double_charge_refund"] == [False, False, False, False]
    assert sum(len(value) for value in ticks.values()) == 67


# --- the graded-cases flag --------------------------------------------------


def test_parse_graded_cases_splits_and_strips() -> None:
    assert parse_graded_cases(" alpha , beta ,, ") == ("alpha", "beta")


def test_parse_graded_cases_rejects_an_empty_list() -> None:
    with pytest.raises(CalibrationError):
        parse_graded_cases("  , ,")


# --- the arithmetic ---------------------------------------------------------


def test_calibration_computes_agreement_on_a_hand_built_example(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    result = calibrate(run_dir=run_dir, graded_cases=("alpha", "beta"))

    assert result.compared == 4
    assert result.agreements == 2
    assert result.agreement_rate == 0.5
    assert result.false_pass == 1
    assert result.false_fail == 1
    assert result.judge_errors == 1
    assert result.not_judged == 0


def test_calibration_lists_every_mismatch(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    result = calibrate(run_dir=run_dir, graded_cases=("alpha", "beta"))

    assert len(result.mismatches) == 2
    false_pass = next(item for item in result.mismatches if item.judge_passed)
    false_fail = next(item for item in result.mismatches if not item.judge_passed)
    assert (false_pass.case_id, false_pass.criterion_index) == ("alpha", 1)
    assert false_pass.human_passed is False
    assert false_pass.judge_reason == "because."
    assert (false_fail.case_id, false_fail.criterion_index) == ("beta", 1)
    assert false_fail.human_passed is True


def test_unlisted_cases_are_ignored_not_failed(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    result = calibrate(run_dir=run_dir, graded_cases=("alpha",))

    assert result.compared == 2
    assert result.graded_cases == ("alpha",)
    assert all(item.case_id == "alpha" for item in result.mismatches)


def test_a_criterion_with_no_verdict_is_counted_as_not_judged(tmp_path: Path) -> None:
    run_dir = build_run_dir(
        tmp_path, verdicts=[verdict_row("alpha", 0, True), verdict_row("alpha", 1, False)]
    )

    result = calibrate(run_dir=run_dir, graded_cases=("alpha",))

    assert result.compared == 2
    assert result.not_judged == 1
    assert result.judge_errors == 0


def test_calibrating_a_case_absent_from_review_is_an_error(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=("alpha", "no_such_case"))


def test_nothing_comparable_is_an_error(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path, verdicts=[])

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=("alpha",))


# --- output -----------------------------------------------------------------


def test_calibration_json_is_written_into_the_run_directory(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    exit_code = main(["--run", str(run_dir), "--graded-cases", "alpha,beta"])

    assert exit_code == 0
    payload = json.loads((run_dir / CALIBRATION_FILENAME).read_text(encoding="utf-8"))
    assert payload["run_id"] == run_dir.name
    assert payload["graded_cases"] == ["alpha", "beta"]
    assert payload["compared"] == 4
    assert payload["agreement_rate"] == 0.5
    assert payload["false_pass"] == 1
    assert payload["false_fail"] == 1
    assert payload["judge_errors"] == 1
    assert len(payload["mismatches"]) == 2


def test_the_printed_table_reports_the_counts(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    result = calibrate(run_dir=run_dir, graded_cases=("alpha", "beta"))

    table = render_table(result)

    assert "Compared" in table
    assert "false_pass" in table
    assert "alpha" in table


def test_cli_exits_two_when_nothing_could_be_compared(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path, verdicts=[])

    exit_code = main(["--run", str(run_dir), "--graded-cases", "alpha"])

    assert exit_code == 2


def test_cli_exits_two_when_the_run_directory_is_missing(tmp_path: Path) -> None:
    exit_code = main(["--run", str(tmp_path / "absent"), "--graded-cases", "alpha"])

    assert exit_code == 2


def test_cli_requires_the_graded_cases_flag(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    with pytest.raises(SystemExit):
        main(["--run", str(run_dir)])


# --- malformed inputs -------------------------------------------------------


def test_unparseable_verdicts_are_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").write_text("not json\n", encoding="utf-8")

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=("alpha",))


def test_a_verdict_row_that_is_not_an_object_is_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").write_text('["alpha"]\n', encoding="utf-8")

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=("alpha",))


def test_a_verdict_row_without_a_case_id_is_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").write_text(
        json.dumps({"sample_index": 0, "judge_sample_index": 0, "passed": True}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=("alpha",))


def test_a_missing_review_file_is_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "review.md").unlink()

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=("alpha",))


def test_calibrate_rejects_an_empty_graded_case_tuple(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)

    with pytest.raises(CalibrationError):
        calibrate(run_dir=run_dir, graded_cases=())


def test_the_table_says_so_when_nothing_disagreed(tmp_path: Path) -> None:
    run_dir = build_run_dir(
        tmp_path, verdicts=[verdict_row("alpha", 0, True), verdict_row("alpha", 1, False)]
    )

    table = render_table(calibrate(run_dir=run_dir, graded_cases=("alpha",)))

    assert "No mismatches." in table
