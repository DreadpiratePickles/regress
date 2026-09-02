"""Tests for the stage 01 runner.

The runner is exercised entirely through FakeProvider; no test calls the network.
"""

import json
from pathlib import Path

import pytest

from regression_detect import runner
from regression_detect.goldens import load_goldens
from regression_detect.providers.base import ProviderConfigError, ProviderTransientError
from regression_detect.providers.fake import FakeProvider
from regression_detect.review import REVIEW_INPUT_MAX_CHARS
from regression_detect.runner import main, run_goldens

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GOLDENS = REPO_ROOT / "goldens" / "cases.yaml"
CASE_COUNT = 15


class AlwaysFailingProvider:
    """A provider that fails every call, to prove a failure is recorded not fatal."""

    model_id = "always-failing"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        self.calls += 1
        raise ProviderTransientError("rate limited")


class OneCaseFailsProvider:
    """Fails only for the ticket containing a marker, succeeds otherwise."""

    model_id = "one-case-fails"

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def complete(self, *, system: str, user: str, temperature: float) -> str:
        if self.marker in user:
            raise ProviderTransientError("rate limited")
        return "A short summary."


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- happy path -------------------------------------------------------------


def test_dry_run_writes_the_three_stage_outputs(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=FakeProvider("A short summary."),
    )

    assert (summary.out_dir / "outputs.jsonl").is_file()
    assert (summary.out_dir / "manifest.json").is_file()
    assert (summary.out_dir / "review.md").is_file()
    assert summary.ok == CASE_COUNT
    assert summary.failed == 0
    assert summary.total == CASE_COUNT


def test_outputs_jsonl_has_one_line_per_sample(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=2,
        provider=FakeProvider("A short summary."),
    )

    rows = read_jsonl(summary.out_dir / "outputs.jsonl")

    assert len(rows) == CASE_COUNT * 2
    assert {row["sample_index"] for row in rows} == {0, 1}
    first = rows[0]
    assert first["case_id"] == "double_charge_refund"
    assert first["output"] == "A short summary."
    assert first["model_id"] == "fake-provider"
    assert len(first["prompt_sha256"]) == 64
    assert isinstance(first["latency_ms"], int)
    assert first["error"] is None


def test_manifest_records_hashes_counts_and_model(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=FakeProvider("A short summary."),
    )

    manifest = json.loads((summary.out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert len(manifest["goldens_sha256"]) == 64
    assert len(manifest["prompt_sha256"]) == 64
    assert manifest["goldens_path"].endswith("cases.yaml")
    assert manifest["prompt_path"].endswith("summarize_v1.md")
    assert manifest["model_id"] == "fake-provider"
    assert manifest["samples"] == 1
    assert manifest["case_count"] == CASE_COUNT
    assert manifest["counts"] == {"ok": CASE_COUNT, "failed": 0, "total": CASE_COUNT}
    assert manifest["started_at_utc"].endswith("Z")
    assert manifest["finished_at_utc"].endswith("Z")


def test_review_lists_every_case_with_its_criteria_as_a_checklist(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=FakeProvider("A short summary."),
    )

    review = (summary.out_dir / "review.md").read_text(encoding="utf-8")
    cases = load_goldens(REAL_GOLDENS)

    for case in cases:
        assert f"## {case.id}" in review
    expected_criteria = sum(len(case.criteria) for case in cases)
    assert review.count("- [ ] ") == expected_criteria


def test_review_truncates_the_oversized_case_and_says_so(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=FakeProvider("A short summary."),
    )

    review = (summary.out_dir / "review.md").read_text(encoding="utf-8")
    oversized = next(
        case
        for case in load_goldens(REAL_GOLDENS)
        if case.id == "forwarded_thread_import_failure"
    )

    assert "truncated" in review.lower()
    assert len(oversized.input) > REVIEW_INPUT_MAX_CHARS
    assert "month end close" in oversized.input
    assert "month end close" not in review


def test_full_ticket_is_sent_even_though_review_truncates(tmp_path: Path) -> None:
    provider = FakeProvider("A short summary.")

    run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=provider,
    )

    sent = "\n".join(call["user"] for call in provider.calls)
    assert "Sent from my iPhone" in sent


# --- partial failure --------------------------------------------------------


def test_a_failing_case_is_recorded_and_does_not_abort_the_run(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=OneCaseFailsProvider(marker="Brightloom"),
    )

    rows = read_jsonl(summary.out_dir / "outputs.jsonl")

    assert len(rows) == CASE_COUNT
    assert summary.failed == 1
    assert summary.ok == CASE_COUNT - 1
    failed = [row for row in rows if row["error"] is not None]
    assert len(failed) == 1
    assert failed[0]["case_id"] == "sarcastic_slow_response"
    assert failed[0]["output"] is None
    assert failed[0]["error_type"] == "ProviderTransientError"
    manifest = json.loads((summary.out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["failed"] == 1


def test_every_case_failing_still_writes_all_three_files(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        provider=AlwaysFailingProvider(),
    )

    assert summary.failed == CASE_COUNT
    assert summary.ok == 0
    assert (summary.out_dir / "review.md").is_file()
    assert (summary.out_dir / "outputs.jsonl").is_file()
    assert (summary.out_dir / "manifest.json").is_file()


# --- CLI --------------------------------------------------------------------


def test_cli_dry_run_exits_zero_and_creates_a_timestamped_run_dir(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"

    exit_code = main(
        [
            "--goldens",
            str(REAL_GOLDENS),
            "--samples",
            "1",
            "--dry-run",
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert exit_code == 0
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "review.md").is_file()


def test_cli_rejects_a_non_positive_sample_count(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--goldens",
            str(REAL_GOLDENS),
            "--samples",
            "0",
            "--dry-run",
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )

    assert exit_code != 0


def test_cli_reports_a_bad_goldens_path_without_a_traceback(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--goldens",
            str(tmp_path / "missing.yaml"),
            "--dry-run",
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )

    assert exit_code != 0


def test_cli_exits_one_when_a_case_fails_but_still_writes_every_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(
        runner,
        "build_provider",
        lambda *, dry_run: OneCaseFailsProvider(marker="Brightloom"),
    )

    exit_code = main(
        [
            "--goldens",
            str(REAL_GOLDENS),
            "--samples",
            "1",
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert exit_code == 1
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    for filename in ("outputs.jsonl", "manifest.json", "review.md"):
        assert (run_dirs[0] / filename).is_file()
    manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["failed"] == 1
    assert manifest["counts"]["ok"] == CASE_COUNT - 1


def test_cli_exits_two_when_the_provider_cannot_be_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*, dry_run: bool) -> None:
        raise ProviderConfigError("GEMINI_API_KEY is not set.")

    monkeypatch.setattr(runner, "build_provider", refuse)

    exit_code = main(
        [
            "--goldens",
            str(REAL_GOLDENS),
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )

    assert exit_code == 2
