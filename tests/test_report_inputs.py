"""Tests for stage 04's reader: turning a compared run directory into `ReportData`.

The synthetic comparison payload is shared with `test_report.py`, which owns the
rendering tests. Everything here is built in `tmp_path`; nothing calls a model or
touches the network.
"""

import json
from pathlib import Path

import pytest

from regression_detect.report import REPORT_FILENAME, main, render_report
from regression_detect.report_inputs import ReportInputError, read_report_data
from test_report import comparison_payload

# --------------------------------------------------------------------------
# reading a run directory, and the CLI
# --------------------------------------------------------------------------


def build_run_dir(tmp_path: Path, *, verdict: str = "REGRESSION") -> Path:
    run_dir = tmp_path / "runs" / "2026-09-02T10-00-00Z"
    run_dir.mkdir(parents=True)
    (run_dir / "comparison.json").write_text(
        json.dumps(comparison_payload(verdict=verdict)), encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "goldens_sha256": "c" * 64,
                "prompt_sha256": "a" * 64,
                "model_id": "target-model",
                "samples": 1,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "judge_manifest.json").write_text(
        json.dumps(
            {
                "goldens_sha256": "c" * 64,
                "judge_prompt_sha256": "b" * 64,
                "judge_model_id": "judge-model",
                "counts": {"verdicts_ok": 8, "judge_errors": 0, "skipped_outputs": 0},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "scores.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "cases": {},
                "overall": {
                    "criteria_total": 8,
                    "passed": 5,
                    "failed": 3,
                    "errored": 0,
                    "pass_rate": 0.625,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "outputs.jsonl").write_text(
        json.dumps(
            {
                "case_id": "refund",
                "sample_index": 0,
                "output": "A summary that forgot the amount.",
                "model_id": "target-model",
                "prompt_sha256": "a" * 64,
                "latency_ms": 1,
                "error": None,
                "error_type": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "verdicts.jsonl").write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "case_id": "refund",
                    "sample_index": 0,
                    "criterion_index": 0,
                    "criterion": "States the amount.",
                    "judge_sample_index": 0,
                    "passed": False,
                    "reason": "No amount appears in the summary.",
                },
                {
                    "case_id": "refund",
                    "sample_index": 0,
                    "criterion_index": 1,
                    "criterion": "Names the issue.",
                    "judge_sample_index": 0,
                    "passed": True,
                    "reason": "The issue is named.",
                },
            )
        ),
        encoding="utf-8",
    )
    return run_dir


def test_read_report_data_pulls_provenance_from_both_manifests(tmp_path: Path) -> None:
    data = read_report_data(build_run_dir(tmp_path))

    assert data.provenance.run_id == "2026-09-02T10-00-00Z"
    assert data.provenance.target_model_id == "target-model"
    assert data.provenance.judge_model_id == "judge-model"
    assert data.provenance.baseline_run_ids == (
        "2026-01-01T00-00-00Z",
        "2026-01-01T01-00-00Z",
    )
    assert data.provenance.samples == 1


def test_read_report_data_collects_the_output_and_reason_for_a_failed_criterion(
    tmp_path: Path,
) -> None:
    data = read_report_data(build_run_dir(tmp_path))

    evidence = {(item.case_id, item.criterion_index): item for item in data.evidence}
    failed = evidence[("refund", 0)]
    assert failed.outputs == ("A summary that forgot the amount.",)
    assert failed.reasons == ("No amount appears in the summary.",)


def test_read_report_data_rejects_a_run_without_a_comparison(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "comparison.json").unlink()

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_read_report_data_rejects_a_comparison_of_an_unknown_schema(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    payload = comparison_payload()
    payload["schema_version"] = 99
    (run_dir / "comparison.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_read_report_data_rejects_a_malformed_comparison(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "comparison.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_cli_writes_the_report_into_the_run_directory(tmp_path: Path, capsys) -> None:
    run_dir = build_run_dir(tmp_path)

    code = main(["--run", str(run_dir)])

    assert code == 0
    written = (run_dir / REPORT_FILENAME).read_text(encoding="utf-8")
    assert "🔴 REGRESSION" in written
    assert REPORT_FILENAME in capsys.readouterr().out


def test_cli_honours_an_explicit_output_path(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    destination = tmp_path / "elsewhere" / "pr.md"

    assert main(["--run", str(run_dir), "--out", str(destination)]) == 0
    assert "REGRESSION" in destination.read_text(encoding="utf-8")
    assert not (run_dir / REPORT_FILENAME).exists()


def test_cli_exits_three_on_a_run_it_cannot_read(tmp_path: Path, capsys) -> None:
    assert main(["--run", str(tmp_path / "absent")]) == 3
    assert "ReportInputError" in capsys.readouterr().err


# --------------------------------------------------------------------------
# what the reader refuses
# --------------------------------------------------------------------------


def test_a_comparison_missing_a_key_the_report_prints_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    payload = comparison_payload()
    del payload["thresholds"]
    (run_dir / "comparison.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReportInputError, match="thresholds"):
        read_report_data(run_dir)


def test_a_comparison_carrying_an_unknown_verdict_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    payload = comparison_payload()
    payload["verdict"] = "PROBABLY_FINE"
    (run_dir / "comparison.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReportInputError, match="verdict"):
        read_report_data(run_dir)


def test_a_comparison_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "comparison.json").write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_a_malformed_baseline_reference_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    payload = comparison_payload()
    payload["baseline_source"] = {"run_ids": [7]}
    (run_dir / "comparison.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReportInputError, match="run_ids"):
        read_report_data(run_dir)


def test_a_comparison_without_a_baseline_reference_reports_no_baseline_runs(
    tmp_path: Path,
) -> None:
    """An older comparison predates the reference; the footer says so rather than lying."""
    run_dir = build_run_dir(tmp_path)
    payload = comparison_payload()
    del payload["baseline_source"]
    (run_dir / "comparison.json").write_text(json.dumps(payload), encoding="utf-8")

    data = read_report_data(run_dir)

    assert data.provenance.baseline_run_ids == ()
    assert "baseline runs —" in render_report(data)


def test_a_missing_stage_01_manifest_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "manifest.json").unlink()

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_a_manifest_without_a_model_id_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "manifest.json").write_text(json.dumps({"samples": 1}), encoding="utf-8")

    with pytest.raises(ReportInputError, match="model_id"):
        read_report_data(run_dir)


def test_a_manifest_without_a_sample_count_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["samples"] = 0
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReportInputError, match="samples"):
        read_report_data(run_dir)


def test_a_missing_judge_manifest_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "judge_manifest.json").unlink()

    with pytest.raises(ReportInputError, match="Judge manifest"):
        read_report_data(run_dir)


def test_a_scores_file_without_an_overall_block_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "scores.json").write_text(json.dumps({"cases": {}}), encoding="utf-8")

    with pytest.raises(ReportInputError, match="overall"):
        read_report_data(run_dir)


def test_a_scores_overall_block_missing_a_count_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "scores.json").write_text(
        json.dumps({"overall": {"passed": 1, "failed": 0}}), encoding="utf-8"
    )

    with pytest.raises(ReportInputError, match="criteria_total"):
        read_report_data(run_dir)


def test_a_missing_verdicts_file_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").unlink()

    with pytest.raises(ReportInputError, match="verdicts"):
        read_report_data(run_dir)


def test_a_verdict_line_that_is_not_json_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").write_text("{not json\n", encoding="utf-8")

    with pytest.raises(ReportInputError, match="line 1"):
        read_report_data(run_dir)


def test_a_verdict_line_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").write_text("[1]\n", encoding="utf-8")

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_a_missing_outputs_file_is_refused(tmp_path: Path) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "outputs.jsonl").unlink()

    with pytest.raises(ReportInputError):
        read_report_data(run_dir)


def test_a_failed_verdict_naming_no_case_is_skipped_rather_than_crashing(
    tmp_path: Path,
) -> None:
    run_dir = build_run_dir(tmp_path)
    (run_dir / "verdicts.jsonl").write_text(
        json.dumps({"passed": False, "reason": "orphan"}) + "\n", encoding="utf-8"
    )

    assert read_report_data(run_dir).evidence == ()
