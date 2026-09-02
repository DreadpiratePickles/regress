"""Tests for the stage 02 judge runner.

Every run here is built by running stage 01 into a tmp directory with a fake
provider, then judging it with another fake provider. No test calls the network.
"""

import json
from pathlib import Path

import pytest

from regression_detect import judge_runner
from regression_detect.goldens import load_goldens
from regression_detect.judge_runner import (
    JUDGE_MANIFEST_FILENAME,
    JUDGED_FILENAME,
    SCORES_FILENAME,
    VERDICTS_FILENAME,
    GoldensMismatchError,
    JudgeRunError,
    judge_run,
    main,
)
from regression_detect.providers.base import ProviderTransientError
from regression_detect.providers.fake import FakeProvider
from regression_detect.runner import run_goldens

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GOLDENS = REPO_ROOT / "goldens" / "cases.yaml"
CASE_COUNT = 15
CRITERIA_TOTAL = 67

PASS_VERDICT = '{"reason": "The summary states it.", "passed": true}'
FAIL_VERDICT = '{"reason": "The summary omits it.", "passed": false}'

UNIQUE_CRITERION = "Does not invent an order number"
"""Belongs to exactly one case, so a provider can fail exactly one judge call."""


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_stage_01_run(tmp_path: Path, *, provider=None) -> Path:
    """Produce a real stage-01 run directory to judge."""
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "runs" / "2026-01-01T00-00-00Z",
        samples=1,
        provider=provider or FakeProvider("The customer reports a problem and wants help."),
    )
    return summary.out_dir


class OneCriterionFailsProvider:
    """Raises for one criterion, answers every other call with a pass verdict."""

    model_id = "one-criterion-fails"

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls = 0

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        self.calls += 1
        if self.marker in user:
            raise ProviderTransientError("rate limited")
        return PASS_VERDICT


class OneCriterionUnparseableProvider:
    """Returns prose for one criterion, valid JSON for the rest."""

    model_id = "one-criterion-unparseable"

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        if self.marker in user:
            return "Yes, I think that one passes."
        return PASS_VERDICT


class SummaryFailingProvider:
    """Fails the stage-01 call for one ticket, so one output row is null."""

    model_id = "summary-failing"

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        if self.marker in user:
            raise ProviderTransientError("rate limited")
        return "The customer reports a problem and wants help."


# --- happy path -------------------------------------------------------------


def test_dry_run_writes_the_four_stage_outputs(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    exit_code = main(["--run", str(run_dir), "--goldens", str(REAL_GOLDENS), "--dry-run"])

    assert exit_code == 0
    for filename in (VERDICTS_FILENAME, JUDGE_MANIFEST_FILENAME, SCORES_FILENAME, JUDGED_FILENAME):
        assert (run_dir / filename).is_file()


def test_one_verdict_row_per_criterion(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(
        run_dir=run_dir,
        goldens_path=REAL_GOLDENS,
        provider=FakeProvider(PASS_VERDICT),
    )

    rows = read_jsonl(run_dir / VERDICTS_FILENAME)
    assert len(rows) == CRITERIA_TOTAL

    first = rows[0]
    assert first["case_id"] == "double_charge_refund"
    assert first["sample_index"] == 0
    assert first["criterion_index"] == 0
    assert first["judge_sample_index"] == 0
    assert first["passed"] is True
    assert first["reason"] == "The summary states it."
    assert first["judge_model_id"] == "fake-provider"
    assert len(first["judge_prompt_sha256"]) == 64
    assert isinstance(first["latency_ms"], int)
    assert first["error"] is None
    assert first["error_type"] is None


def test_criterion_text_is_recorded_verbatim(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT))

    rows = read_jsonl(run_dir / VERDICTS_FILENAME)
    cases = {case.id: case for case in load_goldens(REAL_GOLDENS)}
    for row in rows:
        assert row["criterion"] == cases[row["case_id"]].criteria[row["criterion_index"]]


def test_judge_samples_multiply_the_calls(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(
        run_dir=run_dir,
        goldens_path=REAL_GOLDENS,
        provider=FakeProvider(PASS_VERDICT),
        judge_samples=2,
    )

    rows = read_jsonl(run_dir / VERDICTS_FILENAME)
    assert len(rows) == CRITERIA_TOTAL * 2
    assert {row["judge_sample_index"] for row in rows} == {0, 1}


def test_manifest_records_provenance_and_counts(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT))

    manifest = json.loads((run_dir / JUDGE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    stage_01 = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["run_id"] == run_dir.name
    assert manifest["stage"] == "02_judge"
    assert manifest["judge_model_id"] == "fake-provider"
    assert manifest["judge_prompt_path"].endswith("judge_v1.md")
    assert len(manifest["judge_prompt_sha256"]) == 64
    assert manifest["goldens_sha256"] == stage_01["goldens_sha256"]
    assert manifest["temperature"] == 0.0
    assert manifest["judge_samples"] == 1
    assert manifest["counts"] == {
        "verdicts_ok": CRITERIA_TOTAL,
        "judge_errors": 0,
        "skipped_outputs": 0,
    }
    assert manifest["started_at_utc"].endswith("Z")
    assert manifest["finished_at_utc"].endswith("Z")


def test_scores_are_computed_by_code(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT))

    scores = json.loads((run_dir / SCORES_FILENAME).read_text(encoding="utf-8"))

    assert scores["run_id"] == run_dir.name
    assert len(scores["cases"]) == CASE_COUNT
    assert scores["cases"]["double_charge_refund"] == {
        "criteria_total": 4,
        "passed": 4,
        "failed": 0,
        "errored": 0,
        "pass_rate": 1.0,
    }
    assert scores["overall"] == {
        "criteria_total": CRITERIA_TOTAL,
        "passed": CRITERIA_TOTAL,
        "failed": 0,
        "errored": 0,
        "pass_rate": 1.0,
    }


def test_a_failing_judge_lowers_the_pass_rate(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(FAIL_VERDICT))

    scores = json.loads((run_dir / SCORES_FILENAME).read_text(encoding="utf-8"))

    assert scores["overall"]["passed"] == 0
    assert scores["overall"]["failed"] == CRITERIA_TOTAL
    assert scores["overall"]["pass_rate"] == 0.0


def test_judged_markdown_shows_every_case_and_its_verdicts(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT))

    judged = (run_dir / JUDGED_FILENAME).read_text(encoding="utf-8")

    for case in load_goldens(REAL_GOLDENS):
        assert f"## {case.id}" in judged
    assert judged.count("✅") == CRITERIA_TOTAL
    assert "The summary states it." in judged
    assert "The customer reports a problem and wants help." in judged


# --- partial failure --------------------------------------------------------


def test_a_provider_failure_on_one_criterion_is_recorded_and_the_run_continues(
    tmp_path: Path,
) -> None:
    run_dir = build_stage_01_run(tmp_path)

    summary = judge_run(
        run_dir=run_dir,
        goldens_path=REAL_GOLDENS,
        provider=OneCriterionFailsProvider(marker=UNIQUE_CRITERION),
    )

    rows = read_jsonl(run_dir / VERDICTS_FILENAME)
    errored = [row for row in rows if row["error"] is not None]

    assert len(rows) == CRITERIA_TOTAL
    assert summary.judge_errors == 1
    assert summary.verdicts_ok == CRITERIA_TOTAL - 1
    assert len(errored) == 1
    assert errored[0]["case_id"] == "double_charge_refund"
    assert errored[0]["passed"] is None
    assert errored[0]["reason"] is None
    assert errored[0]["error_type"] == "ProviderTransientError"


def test_an_unparseable_verdict_is_an_error_never_a_failed_criterion(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    judge_run(
        run_dir=run_dir,
        goldens_path=REAL_GOLDENS,
        provider=OneCriterionUnparseableProvider(marker=UNIQUE_CRITERION),
    )

    rows = read_jsonl(run_dir / VERDICTS_FILENAME)
    errored = [row for row in rows if row["error"] is not None]
    scores = json.loads((run_dir / SCORES_FILENAME).read_text(encoding="utf-8"))

    assert len(errored) == 1
    assert errored[0]["error_type"] == "JudgeParseError"
    assert errored[0]["passed"] is None
    assert scores["cases"]["double_charge_refund"] == {
        "criteria_total": 4,
        "passed": 3,
        "failed": 0,
        "errored": 1,
        "pass_rate": 1.0,
    }
    assert scores["overall"]["failed"] == 0
    assert scores["overall"]["errored"] == 1


def test_a_null_stage_01_output_is_skipped_and_counted(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(
        tmp_path, provider=SummaryFailingProvider(marker="Brightloom")
    )

    summary = judge_run(
        run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT)
    )

    rows = read_jsonl(run_dir / VERDICTS_FILENAME)
    scores = json.loads((run_dir / SCORES_FILENAME).read_text(encoding="utf-8"))

    assert summary.skipped_outputs == 1
    assert not [row for row in rows if row["case_id"] == "sarcastic_slow_response"]
    assert scores["cases"]["sarcastic_slow_response"] == {
        "criteria_total": 0,
        "passed": 0,
        "failed": 0,
        "errored": 0,
        "pass_rate": None,
    }
    judged = (run_dir / JUDGED_FILENAME).read_text(encoding="utf-8")
    assert "not judged" in judged.lower()


def test_a_run_where_nothing_could_be_judged_has_a_null_overall_pass_rate(
    tmp_path: Path,
) -> None:
    run_dir = build_stage_01_run(tmp_path)
    (run_dir / "outputs.jsonl").write_text(
        json.dumps(
            {
                "case_id": "double_charge_refund",
                "sample_index": 0,
                "output": None,
                "model_id": "x",
                "prompt_sha256": "y",
                "latency_ms": 1,
                "error": "boom",
                "error_type": "ProviderTransientError",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT))

    scores = json.loads((run_dir / SCORES_FILENAME).read_text(encoding="utf-8"))
    assert scores["overall"]["pass_rate"] is None


# --- configuration failures -------------------------------------------------


def test_a_goldens_mismatch_aborts_with_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    other = tmp_path / "other_cases.yaml"
    other.write_text(
        REAL_GOLDENS.read_text(encoding="utf-8") + "\n# a later edit\n", encoding="utf-8"
    )

    with pytest.raises(GoldensMismatchError):
        judge_run(
            run_dir=run_dir, goldens_path=other, provider=FakeProvider(PASS_VERDICT)
        )


def test_cli_exits_two_on_a_goldens_mismatch(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    other = tmp_path / "other_cases.yaml"
    other.write_text(
        REAL_GOLDENS.read_text(encoding="utf-8") + "\n# a later edit\n", encoding="utf-8"
    )

    exit_code = main(["--run", str(run_dir), "--goldens", str(other), "--dry-run"])

    assert exit_code == 2


def test_cli_exits_two_when_the_run_directory_is_missing(tmp_path: Path) -> None:
    exit_code = main(
        ["--run", str(tmp_path / "absent"), "--goldens", str(REAL_GOLDENS), "--dry-run"]
    )

    assert exit_code == 2


def test_cli_exits_two_when_outputs_are_unparseable(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    (run_dir / "outputs.jsonl").write_text("not json\n", encoding="utf-8")

    exit_code = main(["--run", str(run_dir), "--goldens", str(REAL_GOLDENS), "--dry-run"])

    assert exit_code == 2


def test_an_output_row_naming_an_unknown_case_is_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    (run_dir / "outputs.jsonl").write_text(
        json.dumps(
            {
                "case_id": "no_such_case",
                "sample_index": 0,
                "output": "text",
                "model_id": "x",
                "prompt_sha256": "y",
                "latency_ms": 1,
                "error": None,
                "error_type": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(JudgeRunError):
        judge_run(
            run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT)
        )


def test_cli_rejects_a_non_positive_judge_sample_count(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    exit_code = main(
        [
            "--run",
            str(run_dir),
            "--goldens",
            str(REAL_GOLDENS),
            "--dry-run",
            "--judge-samples",
            "0",
        ]
    )

    assert exit_code == 2


def test_cli_exits_one_when_a_judge_call_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = build_stage_01_run(tmp_path)
    monkeypatch.setattr(
        judge_runner,
        "_build_provider",
        lambda *, dry_run: OneCriterionFailsProvider(marker=UNIQUE_CRITERION),
    )

    exit_code = main(["--run", str(run_dir), "--goldens", str(REAL_GOLDENS)])

    assert exit_code == 1
    manifest = json.loads((run_dir / JUDGE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["counts"]["judge_errors"] == 1


# --- malformed stage-01 artifacts -------------------------------------------


def write_outputs(run_dir: Path, rows: list) -> None:
    (run_dir / "outputs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


BASE_OUTPUT_ROW = {
    "case_id": "double_charge_refund",
    "sample_index": 0,
    "output": "a summary",
    "model_id": "x",
    "prompt_sha256": "y",
    "latency_ms": 1,
    "error": None,
    "error_type": None,
}


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(["not", "an", "object"], id="not_an_object"),
        pytest.param({**BASE_OUTPUT_ROW, "case_id": 7}, id="case_id_not_a_string"),
        pytest.param({**BASE_OUTPUT_ROW, "case_id": ""}, id="case_id_empty"),
        pytest.param({**BASE_OUTPUT_ROW, "sample_index": "0"}, id="sample_index_not_an_int"),
        pytest.param({**BASE_OUTPUT_ROW, "sample_index": True}, id="sample_index_a_bool"),
        pytest.param({**BASE_OUTPUT_ROW, "output": 12}, id="output_not_a_string"),
    ],
)
def test_a_malformed_output_row_is_a_typed_error(tmp_path: Path, row: object) -> None:
    run_dir = build_stage_01_run(tmp_path)
    write_outputs(run_dir, [row])

    with pytest.raises(JudgeRunError):
        judge_run(
            run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT)
        )


def test_blank_lines_in_outputs_are_skipped(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    (run_dir / "outputs.jsonl").write_text(
        json.dumps(BASE_OUTPUT_ROW) + "\n\n   \n", encoding="utf-8"
    )

    summary = judge_run(
        run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT)
    )

    assert summary.verdicts_ok == 4


def test_a_stage_01_manifest_without_a_goldens_hash_is_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": "x"}), encoding="utf-8")

    with pytest.raises(JudgeRunError):
        judge_run(
            run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT)
        )


def test_a_missing_stage_01_outputs_file_is_a_typed_error(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)
    (run_dir / "outputs.jsonl").unlink()

    with pytest.raises(JudgeRunError):
        judge_run(
            run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT)
        )


# --- pacing ------------------------------------------------------------------


def test_by_default_the_judge_does_not_pace_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = build_stage_01_run(tmp_path)
    slept: list[float] = []
    monkeypatch.setattr(judge_runner.time, "sleep", slept.append)

    judge_run(run_dir=run_dir, goldens_path=REAL_GOLDENS, provider=FakeProvider(PASS_VERDICT))

    assert slept == []


def test_pacing_waits_between_consecutive_judge_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = build_stage_01_run(tmp_path)
    slept: list[float] = []
    monkeypatch.setattr(judge_runner.time, "sleep", slept.append)

    judge_run(
        run_dir=run_dir,
        goldens_path=REAL_GOLDENS,
        provider=FakeProvider(PASS_VERDICT),
        min_interval_ms=250,
    )

    assert len(slept) == CRITERIA_TOTAL - 1
    assert all(0 < wait <= 0.25 for wait in slept)


def test_a_negative_interval_is_rejected(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    with pytest.raises(ValueError):
        judge_run(
            run_dir=run_dir,
            goldens_path=REAL_GOLDENS,
            provider=FakeProvider(PASS_VERDICT),
            min_interval_ms=-1,
        )


def test_cli_rejects_a_negative_interval(tmp_path: Path) -> None:
    run_dir = build_stage_01_run(tmp_path)

    exit_code = main(
        [
            "--run",
            str(run_dir),
            "--goldens",
            str(REAL_GOLDENS),
            "--dry-run",
            "--min-interval-ms",
            "-1",
        ]
    )

    assert exit_code == 2
