"""Tests for stage 03's entry points: the compare CLI and the detect pipeline.

The pipeline test runs stages 01, 02 and 03 end to end against the real golden
dataset with the canned provider, in a tmp directory. Nothing here touches the
network and nothing writes outside `tmp_path`.
"""

import json
from pathlib import Path

import pytest

from regression_detect import compare_run, pipeline, runner
from regression_detect.baseline import Baseline, CriterionStat, build_baseline
from regression_detect.compare import COMPARISON_FILENAME, Verdict
from regression_detect.goldens import goldens_sha256, load_goldens
from regression_detect.judge_runner import VERDICTS_FILENAME
from regression_detect.report import REPORT_FILENAME
from regression_detect.target.summarizer import DEFAULT_PROMPT_PATH

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GOLDENS = REPO_ROOT / "goldens" / "cases.yaml"
REAL_CONFIG = REPO_ROOT / "regression.toml"
CRITERIA_TOTAL = 67


def fake_baseline(*, passes_per_criterion: int = 1, n: int = 1) -> Baseline:
    """A baseline over the real criteria, built without running anything."""
    stats = [
        CriterionStat(
            case_id=case.id,
            criterion_index=index,
            criterion=criterion,
            n=n,
            passes=passes_per_criterion,
            judge_errors=0,
        )
        for case in load_goldens(REAL_GOLDENS)
        for index, criterion in enumerate(case.criteria)
    ]
    return Baseline(
        schema_version=1,
        created_at_utc="2026-01-01T00:00:00Z",
        run_ids=("baseline-run",),
        goldens_sha256=goldens_sha256(REAL_GOLDENS),
        prompt_sha256="0" * 64,
        target_model_id="dry-run-fake",
        judge_prompt_sha256="0" * 64,
        judge_model_id="dry-run-fake-judge",
        criteria=tuple(stats),
        total_n=sum(item.n for item in stats),
        total_passes=sum(item.passes for item in stats),
        judge_errors=0,
    )


def write_baseline(tmp_path: Path, baseline: Baseline | None = None) -> Path:
    path = tmp_path / "baselines" / "baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (baseline or fake_baseline()).to_json()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def detect_args(tmp_path: Path, baseline_path: Path, *extra: str) -> list[str]:
    return [
        "--baseline",
        str(baseline_path),
        "--goldens",
        str(REAL_GOLDENS),
        "--prompt",
        str(REPO_ROOT / DEFAULT_PROMPT_PATH),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--config",
        str(REAL_CONFIG),
        "--dry-run",
        *extra,
    ]


# --------------------------------------------------------------------------
# the detect pipeline, end to end and offline
# --------------------------------------------------------------------------


def test_dry_run_pipeline_runs_all_three_stages(tmp_path, capsys):
    baseline_path = write_baseline(tmp_path)

    code = pipeline.main(detect_args(tmp_path, baseline_path, "--samples", "1"))
    printed = capsys.readouterr().out

    run_dirs = sorted((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for filename in ("outputs.jsonl", "manifest.json", "review.md"):
        assert (run_dir / filename).exists()
    for filename in (VERDICTS_FILENAME, "judge_manifest.json", "scores.json", "judged.md"):
        assert (run_dir / filename).exists()
    assert (run_dir / COMPARISON_FILENAME).exists()

    assert code == 0
    assert "NO_REGRESSION" in printed
    assert str(run_dir.name) in printed


def test_dry_run_pipeline_also_writes_the_pr_report(tmp_path):
    baseline_path = write_baseline(tmp_path)
    pipeline.main(detect_args(tmp_path, baseline_path, "--samples", "1"))

    run_dir = sorted((tmp_path / "runs").iterdir())[0]
    report = (run_dir / REPORT_FILENAME).read_text(encoding="utf-8")

    assert "🟢 NO_REGRESSION" in report
    assert run_dir.name in report


def test_the_comparison_records_which_baseline_it_was_measured_against(tmp_path):
    baseline_path = write_baseline(tmp_path)
    pipeline.main(detect_args(tmp_path, baseline_path, "--samples", "1"))

    run_dir = sorted((tmp_path / "runs").iterdir())[0]
    payload = json.loads((run_dir / COMPARISON_FILENAME).read_text(encoding="utf-8"))

    assert payload["baseline_source"]["run_ids"] == ["baseline-run"]
    assert payload["baseline_source"]["created_at_utc"] == "2026-01-01T00:00:00Z"


def test_dry_run_comparison_matches_every_criterion(tmp_path):
    baseline_path = write_baseline(tmp_path)
    pipeline.main(detect_args(tmp_path, baseline_path, "--samples", "1"))

    run_dir = sorted((tmp_path / "runs").iterdir())[0]
    payload = json.loads((run_dir / COMPARISON_FILENAME).read_text(encoding="utf-8"))

    assert payload["verdict"] == "NO_REGRESSION"
    assert payload["unmatched"] == []
    assert len(payload["criteria"]) == CRITERIA_TOTAL
    assert payload["overall"]["candidate"]["n"] == CRITERIA_TOTAL
    assert payload["overall"]["candidate"]["passes"] == CRITERIA_TOTAL


def test_dry_run_candidate_can_be_rebuilt_into_a_baseline(tmp_path):
    """The candidate is a `Baseline` too — that is what makes re-baselining one step."""
    baseline_path = write_baseline(tmp_path)
    pipeline.main(detect_args(tmp_path, baseline_path, "--samples", "1"))

    run_dir = sorted((tmp_path / "runs").iterdir())[0]
    rebuilt = build_baseline([run_dir])

    assert rebuilt.total_n == CRITERIA_TOTAL
    assert rebuilt.total_passes == CRITERIA_TOTAL
    assert rebuilt.run_ids == (run_dir.name,)


def test_pipeline_uses_the_sample_count_from_the_config_by_default(tmp_path):
    baseline_path = write_baseline(tmp_path)
    pipeline.main(detect_args(tmp_path, baseline_path))

    run_dir = sorted((tmp_path / "runs").iterdir())[0]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["samples"] == 1


@pytest.mark.parametrize("bad", [["--samples", "0"], ["--min-interval-ms", "-1"]])
def test_pipeline_rejects_impossible_pacing_or_sample_counts(tmp_path, capsys, bad):
    baseline_path = write_baseline(tmp_path)
    assert pipeline.main(detect_args(tmp_path, baseline_path, *bad)) == 3
    assert "ValueError" in capsys.readouterr().err


def test_pipeline_reports_a_missing_baseline_as_a_config_error(tmp_path, capsys):
    code = pipeline.main(detect_args(tmp_path, tmp_path / "absent.json", "--samples", "1"))

    assert code == 3
    assert "BaselineInputError" in capsys.readouterr().err


def test_pipeline_reports_a_missing_config_file(tmp_path, capsys):
    args = detect_args(tmp_path, write_baseline(tmp_path), "--samples", "1")
    args[args.index("--config") + 1] = str(tmp_path / "absent.toml")

    assert pipeline.main(args) == 3
    assert "ConfigFileError" in capsys.readouterr().err


def test_pipeline_reports_a_missing_goldens_file(tmp_path, capsys):
    args = detect_args(tmp_path, write_baseline(tmp_path), "--samples", "1")
    args[args.index("--goldens") + 1] = str(tmp_path / "absent.yaml")

    assert pipeline.main(args) == 3
    assert "GoldenDatasetError" in capsys.readouterr().err


def test_pacing_applies_to_the_target_calls_as_well_as_the_judge(tmp_path, monkeypatch):
    """A per-minute quota is spent by stage 01 too, so both stages are paced."""
    slept: list[float] = []
    monkeypatch.setattr(runner.time, "sleep", slept.append)
    baseline_path = write_baseline(tmp_path)

    pipeline.main(detect_args(tmp_path, baseline_path, "--samples", "1",
                              "--min-interval-ms", "20"))

    assert len(slept) == 15 - 1 + CRITERIA_TOTAL - 1


def test_detect_returns_the_run_directory_and_result(tmp_path):
    outcome = pipeline.detect(
        baseline_path=write_baseline(tmp_path),
        goldens_path=REAL_GOLDENS,
        prompt_path=REPO_ROOT / DEFAULT_PROMPT_PATH,
        runs_dir=tmp_path / "runs",
        config_path=REAL_CONFIG,
        samples=1,
        min_interval_ms=0,
        dry_run=True,
    )

    assert outcome.run_dir.exists()
    assert outcome.comparison.verdict is Verdict.NO_REGRESSION
    assert outcome.run_summary.total == 15
    assert outcome.judge_summary.judge_errors == 0
    assert outcome.exit_code == 0


# --------------------------------------------------------------------------
# the compare CLI
# --------------------------------------------------------------------------


def write_judged_run(tmp_path: Path, run_id: str, *, passes: int, total: int = 40) -> Path:
    """A synthetic judged run directory: the three files stage 03 reads."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    provenance = {
        "goldens_sha256": "1" * 64,
        "prompt_sha256": "2" * 64,
        "model_id": "synthetic-target",
    }
    (run_dir / "manifest.json").write_text(json.dumps(provenance), encoding="utf-8")
    (run_dir / "judge_manifest.json").write_text(
        json.dumps(
            {
                "goldens_sha256": "1" * 64,
                "judge_prompt_sha256": "3" * 64,
                "judge_model_id": "synthetic-judge",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / VERDICTS_FILENAME).write_text(
        "".join(
            json.dumps(
                {
                    "case_id": "synthetic",
                    "sample_index": 0,
                    "criterion_index": index,
                    "criterion": f"criterion {index}",
                    "judge_sample_index": 0,
                    "passed": index < passes,
                }
            )
            + "\n"
            for index in range(total)
        ),
        encoding="utf-8",
    )
    return run_dir


def baseline_from(tmp_path: Path, run_dirs: list[Path], name: str = "baseline.json") -> Path:
    path = tmp_path / name
    payload = build_baseline(run_dirs).to_json()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def cli_args(baseline_path: Path, candidate: Path) -> list[str]:
    return [
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate),
        "--config",
        str(REAL_CONFIG),
    ]


def test_compare_cli_writes_comparison_json_and_exits_zero(tmp_path, capsys):
    baseline_run = write_judged_run(tmp_path, "baseline", passes=40)
    candidate = write_judged_run(tmp_path, "candidate", passes=40)
    baseline_path = baseline_from(tmp_path, [baseline_run])

    code = compare_run.main(cli_args(baseline_path, candidate))

    assert code == 0
    written = json.loads((candidate / COMPARISON_FILENAME).read_text(encoding="utf-8"))
    assert written["verdict"] == "NO_REGRESSION"
    assert "NO_REGRESSION" in capsys.readouterr().out


def test_compare_cli_exits_one_on_a_regression(tmp_path, capsys):
    baseline_run = write_judged_run(tmp_path, "baseline", passes=39)
    candidate = write_judged_run(tmp_path, "candidate", passes=25)
    baseline_path = baseline_from(tmp_path, [baseline_run])

    code = compare_run.main(cli_args(baseline_path, candidate))
    printed = capsys.readouterr().out

    assert code == 1
    assert "REGRESSION" in printed
    assert "Pass rate fell" in printed


def test_compare_cli_prints_the_flagged_criteria(tmp_path, capsys):
    baseline_run = write_judged_run(tmp_path, "baseline", passes=39)
    candidate = write_judged_run(tmp_path, "candidate", passes=25)
    baseline_path = baseline_from(tmp_path, [baseline_run])

    compare_run.main(cli_args(baseline_path, candidate))
    printed = capsys.readouterr().out

    assert "criterion 30" in printed
    assert "1/1" in printed


def test_compare_cli_exits_two_when_inconclusive(tmp_path, capsys):
    baseline_run = write_judged_run(tmp_path, "baseline", passes=10, total=10)
    candidate = write_judged_run(tmp_path, "candidate", passes=9, total=10)
    baseline_path = baseline_from(tmp_path, [baseline_run])

    code = compare_run.main(cli_args(baseline_path, candidate))

    assert code == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_compare_cli_reports_unmatched_criteria(tmp_path, capsys):
    baseline_run = write_judged_run(tmp_path, "baseline", passes=40, total=40)
    candidate = write_judged_run(tmp_path, "candidate", passes=41, total=41)
    baseline_path = baseline_from(tmp_path, [baseline_run])

    compare_run.main(cli_args(baseline_path, candidate))
    printed = capsys.readouterr().out

    assert "goldens changed" in printed
    assert "unmatched" in printed.lower()


def test_compare_cli_exits_three_on_a_bad_candidate(tmp_path, capsys):
    baseline_path = baseline_from(tmp_path, [write_judged_run(tmp_path, "baseline", passes=40)])

    code = compare_run.main(cli_args(baseline_path, tmp_path / "absent"))

    assert code == 3
    assert "BaselineInputError" in capsys.readouterr().err


def test_compare_cli_exits_three_on_a_bad_baseline_file(tmp_path, capsys):
    candidate = write_judged_run(tmp_path, "candidate", passes=40)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    assert compare_run.main(cli_args(broken, candidate)) == 3
    assert "BaselineInputError" in capsys.readouterr().err


def test_compare_cli_exits_three_on_a_bad_config(tmp_path, capsys):
    candidate = write_judged_run(tmp_path, "candidate", passes=40)
    baseline_path = baseline_from(tmp_path, [write_judged_run(tmp_path, "baseline", passes=40)])

    code = compare_run.main(
        [
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate),
            "--config",
            str(tmp_path / "absent.toml"),
        ]
    )

    assert code == 3
    assert "ConfigFileError" in capsys.readouterr().err
