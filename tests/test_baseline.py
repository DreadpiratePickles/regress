"""Tests for stage 03's aggregation step.

Every run directory here is synthesised on disk in a tmp path: three files, the
same three a real judged run leaves behind. No test calls the network.
"""

import json
from pathlib import Path

import pytest

from regression_detect.baseline import (
    SCHEMA_VERSION,
    Baseline,
    BaselineInputError,
    build_baseline,
    main,
    render_table,
)

GOLDENS_SHA = "1" * 64
PROMPT_SHA = "2" * 64
JUDGE_PROMPT_SHA = "3" * 64
TARGET_MODEL = "test-target-model"
JUDGE_MODEL = "test-judge-model"


def verdict_row(case_id: str, criterion_index: int, passed: bool | None, **overrides) -> dict:
    row = {
        "case_id": case_id,
        "sample_index": 0,
        "criterion_index": criterion_index,
        "criterion": f"{case_id} criterion {criterion_index}",
        "judge_sample_index": 0,
        "passed": passed,
        "reason": None if passed is None else "because",
        "judge_model_id": JUDGE_MODEL,
        "judge_prompt_sha256": JUDGE_PROMPT_SHA,
        "latency_ms": 1,
        "error_type": "ProviderTransientError" if passed is None else None,
        "error": "rate limited" if passed is None else None,
    }
    row.update(overrides)
    return row


def write_run(
    root: Path,
    run_id: str,
    rows: list[dict],
    *,
    goldens_sha: str = GOLDENS_SHA,
    prompt_sha: str = PROMPT_SHA,
    judge_prompt_sha: str = JUDGE_PROMPT_SHA,
    target_model: str = TARGET_MODEL,
    judge_model: str = JUDGE_MODEL,
    omit: tuple[str, ...] = (),
) -> Path:
    """Write the three files stage 03 reads out of a judged run directory."""
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "stage": "01_run",
        "goldens_sha256": goldens_sha,
        "prompt_sha256": prompt_sha,
        "model_id": target_model,
        "counts": {"ok": 1, "failed": 0, "total": 1},
    }
    judge_manifest = {
        "run_id": run_id,
        "stage": "02_judge",
        "goldens_sha256": goldens_sha,
        "judge_prompt_sha256": judge_prompt_sha,
        "judge_model_id": judge_model,
        "counts": {"verdicts_ok": len(rows), "judge_errors": 0, "skipped_outputs": 0},
    }

    if "manifest.json" not in omit:
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if "judge_manifest.json" not in omit:
        (run_dir / "judge_manifest.json").write_text(json.dumps(judge_manifest), encoding="utf-8")
    if "verdicts.jsonl" not in omit:
        (run_dir / "verdicts.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    return run_dir


def two_runs(tmp_path: Path) -> list[Path]:
    """Two runs over the same two criteria: 2/2 on the first, 1/2 on the second."""
    first = write_run(
        tmp_path,
        "run-a",
        [verdict_row("alpha", 0, True), verdict_row("alpha", 1, True)],
    )
    second = write_run(
        tmp_path,
        "run-b",
        [verdict_row("alpha", 0, True), verdict_row("alpha", 1, False)],
    )
    return [first, second]


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def test_build_baseline_sums_two_runs_per_criterion(tmp_path):
    baseline = build_baseline(two_runs(tmp_path))

    stats = {item.criterion_index: item for item in baseline.criteria}
    assert (stats[0].n, stats[0].passes) == (2, 2)
    assert (stats[1].n, stats[1].passes) == (2, 1)
    assert (baseline.total_n, baseline.total_passes) == (4, 3)
    assert baseline.judge_errors == 0


def test_build_baseline_records_provenance_and_source_runs(tmp_path):
    baseline = build_baseline(two_runs(tmp_path))

    assert baseline.run_ids == ("run-a", "run-b")
    assert baseline.goldens_sha256 == GOLDENS_SHA
    assert baseline.prompt_sha256 == PROMPT_SHA
    assert baseline.target_model_id == TARGET_MODEL
    assert baseline.judge_prompt_sha256 == JUDGE_PROMPT_SHA
    assert baseline.judge_model_id == JUDGE_MODEL
    assert baseline.schema_version == SCHEMA_VERSION
    assert baseline.created_at_utc.endswith("Z")


def test_build_baseline_keeps_the_criterion_text(tmp_path):
    baseline = build_baseline(two_runs(tmp_path))
    assert baseline.criteria[0].criterion == "alpha criterion 0"


def test_judge_errors_are_excluded_from_n_and_counted_separately(tmp_path):
    run = write_run(
        tmp_path,
        "run-a",
        [
            verdict_row("alpha", 0, True),
            verdict_row("alpha", 0, None),
            verdict_row("alpha", 1, False),
        ],
    )
    baseline = build_baseline([run])

    stats = {item.criterion_index: item for item in baseline.criteria}
    assert (stats[0].n, stats[0].passes, stats[0].judge_errors) == (1, 1, 1)
    assert (stats[1].n, stats[1].passes, stats[1].judge_errors) == (1, 0, 0)
    assert (baseline.total_n, baseline.total_passes) == (2, 1)
    assert baseline.judge_errors == 1


def test_criteria_are_sorted_by_case_then_index(tmp_path):
    run = write_run(
        tmp_path,
        "run-a",
        [
            verdict_row("beta", 0, True),
            verdict_row("alpha", 1, True),
            verdict_row("alpha", 0, True),
        ],
    )
    baseline = build_baseline([run])
    assert [(item.case_id, item.criterion_index) for item in baseline.criteria] == [
        ("alpha", 0),
        ("alpha", 1),
        ("beta", 0),
    ]


def test_a_criterion_with_only_errors_has_a_null_pass_rate(tmp_path):
    run = write_run(tmp_path, "run-a", [verdict_row("alpha", 0, None)])
    baseline = build_baseline([run])

    assert baseline.criteria[0].n == 0
    assert baseline.criteria[0].pass_rate is None
    assert baseline.total_n == 0


# --------------------------------------------------------------------------
# refusing to aggregate runs that are not comparable
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("goldens_sha", "9" * 64, "goldens_sha256"),
        ("prompt_sha", "9" * 64, "prompt_sha256"),
        ("target_model", "other-model", "model_id"),
        ("judge_prompt_sha", "9" * 64, "judge_prompt_sha256"),
        ("judge_model", "other-judge", "judge_model_id"),
    ],
)
def test_runs_that_disagree_on_provenance_are_refused(tmp_path, field, value, fragment):
    first = write_run(tmp_path, "run-a", [verdict_row("alpha", 0, True)])
    second = write_run(tmp_path, "run-b", [verdict_row("alpha", 0, True)], **{field: value})

    with pytest.raises(BaselineInputError, match=fragment):
        build_baseline([first, second])


def test_no_run_directories_is_refused(tmp_path):
    with pytest.raises(BaselineInputError, match="at least one"):
        build_baseline([])


@pytest.mark.parametrize(
    "missing", ["manifest.json", "judge_manifest.json", "verdicts.jsonl"]
)
def test_a_run_missing_one_of_its_three_files_is_refused(tmp_path, missing):
    run = write_run(tmp_path, "run-a", [verdict_row("alpha", 0, True)], omit=(missing,))
    with pytest.raises(BaselineInputError, match=missing.split(".")[0]):
        build_baseline([run])


def test_a_verdicts_line_that_is_not_json_is_refused(tmp_path):
    run = write_run(tmp_path, "run-a", [verdict_row("alpha", 0, True)])
    (run / "verdicts.jsonl").write_text("{not json\n", encoding="utf-8")

    with pytest.raises(BaselineInputError, match="line 1"):
        build_baseline([run])


@pytest.mark.parametrize(
    "overrides",
    [
        {"case_id": ""},
        {"case_id": 3},
        {"criterion_index": "0"},
        {"criterion": ""},
        {"passed": "true"},
    ],
)
def test_a_malformed_verdict_row_is_refused(tmp_path, overrides):
    run = write_run(tmp_path, "run-a", [{**verdict_row("alpha", 0, True), **overrides}])
    with pytest.raises(BaselineInputError):
        build_baseline([run])


def test_a_judge_manifest_missing_its_model_id_is_refused(tmp_path):
    run = write_run(tmp_path, "run-a", [verdict_row("alpha", 0, True)])
    (run / "judge_manifest.json").write_text(
        json.dumps({"run_id": "run-a", "judge_prompt_sha256": JUDGE_PROMPT_SHA}), encoding="utf-8"
    )

    with pytest.raises(BaselineInputError, match="judge_model_id"):
        build_baseline([run])


def test_a_run_with_no_verdicts_at_all_is_refused(tmp_path):
    run = write_run(tmp_path, "run-a", [])
    with pytest.raises(BaselineInputError, match="no verdict"):
        build_baseline([run])


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------


def test_to_json_and_back_is_lossless(tmp_path):
    baseline = build_baseline(two_runs(tmp_path))
    restored = Baseline.from_json(json.loads(json.dumps(baseline.to_json())))
    assert restored == baseline


def test_to_json_declares_the_schema_version(tmp_path):
    payload = build_baseline(two_runs(tmp_path)).to_json()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["totals"] == {"n": 4, "passes": 3, "judge_errors": 0}


@pytest.mark.parametrize(
    "payload", ["not a mapping", [], {"schema_version": 999}, {}]
)
def test_from_json_refuses_a_payload_it_does_not_understand(payload):
    with pytest.raises(BaselineInputError):
        Baseline.from_json(payload)


def test_from_json_refuses_a_malformed_criterion(tmp_path):
    payload = build_baseline(two_runs(tmp_path)).to_json()
    payload["criteria"][0] = {"case_id": "alpha"}
    with pytest.raises(BaselineInputError):
        Baseline.from_json(payload)


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def test_build_subcommand_writes_the_baseline(tmp_path, capsys):
    runs = two_runs(tmp_path)
    destination = tmp_path / "baselines" / "baseline.json"

    code = main(["build", "--runs", str(runs[0]), str(runs[1]), "--out", str(destination)])

    assert code == 0
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["totals"]["n"] == 4
    assert "run-a" in capsys.readouterr().out


def test_show_subcommand_prints_the_table(tmp_path, capsys):
    runs = two_runs(tmp_path)
    destination = tmp_path / "baseline.json"
    main(["build", "--runs", str(runs[0]), str(runs[1]), "--out", str(destination)])
    capsys.readouterr()

    assert main(["show", "--baseline", str(destination)]) == 0
    printed = capsys.readouterr().out
    assert "alpha" in printed
    assert "2/2" in printed


def test_build_subcommand_reports_a_bad_run_directory(tmp_path, capsys):
    code = main(["build", "--runs", str(tmp_path / "nope"), "--out", str(tmp_path / "b.json")])

    assert code == 2
    assert "BaselineInputError" in capsys.readouterr().err


def test_show_subcommand_reports_a_missing_baseline(tmp_path, capsys):
    assert main(["show", "--baseline", str(tmp_path / "missing.json")]) == 2
    assert "BaselineInputError" in capsys.readouterr().err


def test_show_subcommand_reports_an_unreadable_baseline(tmp_path, capsys):
    path = tmp_path / "baseline.json"
    path.write_text("{not json", encoding="utf-8")

    assert main(["show", "--baseline", str(path)]) == 2
    assert "BaselineInputError" in capsys.readouterr().err


def test_render_table_shows_totals_and_provenance(tmp_path):
    table = render_table(build_baseline(two_runs(tmp_path)))

    assert TARGET_MODEL in table
    assert JUDGE_MODEL in table
    assert "3/4" in table
    assert "run-a, run-b" in table
