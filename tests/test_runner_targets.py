"""Stage 01 driven through the `Target` seam rather than the built-in summarizer.

Kept out of `test_run_goldens.py` only to keep both files under the 400-line
limit; these are the stage-01 tests for target-agnostic running.

What matters here is that the runner calls `target.run(case.input)`, records
`target.provenance()` in the manifest, and still writes the `prompt_sha256` and
`model_id` keys the baseline and the comparison were built on.
"""

import json
import sys
from pathlib import Path

import pytest

from regression_detect.providers.fake import FakeProvider
from regression_detect.runner import build_target, main, run_goldens
from regression_detect.target.adapters.base import TargetExecutionError
from regression_detect.target.adapters.builtin import BuiltinSummarizerTarget

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GOLDENS = REPO_ROOT / "goldens" / "cases.yaml"
REAL_CONFIG = REPO_ROOT / "regression.toml"
FIXTURE_APP = Path(__file__).resolve().parent / "fixtures" / "fake_target_app.py"
CASE_COUNT = 15

EXTERNAL_CONFIG = """
[compare]
alpha = 0.05
min_effect = 0.05
min_samples = 30
max_judge_error_rate = 0.2

[run]
samples = 1

[models]
target_model_id_env = "TARGET_MODEL_ID"
judge_model_id_env = "JUDGE_MODEL_ID"

[target]
kind = "command"
argv = {argv}
timeout_s = 30.0
"""


class StubTarget:
    """A target with no provider and no prompt, to prove the seam is real."""

    target_id = "stub_target"

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.inputs: list[str] = []
        self.fail_on = fail_on

    def run(self, input_text: str) -> str:
        self.inputs.append(input_text)
        if self.fail_on is not None and self.fail_on in input_text:
            raise TargetExecutionError("the external app exited 3")
        return f"stub summary of {len(input_text)} characters"

    def provenance(self) -> dict[str, str]:
        return {"kind": "stub", "target_id": self.target_id, "app_version": "1.4.2"}


def read_manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- an injected target -----------------------------------------------------


def test_an_injected_target_receives_every_case_input(tmp_path: Path) -> None:
    target = StubTarget()

    summary = run_goldens(
        goldens_path=REAL_GOLDENS, out_dir=tmp_path / "run", samples=2, target=target
    )

    assert summary.ok == CASE_COUNT * 2
    assert len(target.inputs) == CASE_COUNT * 2
    assert all(text.strip() for text in target.inputs)


def test_the_manifest_records_the_target_provenance(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS, out_dir=tmp_path / "run", samples=1, target=StubTarget()
    )

    manifest = read_manifest(summary.out_dir)

    assert manifest["target_id"] == "stub_target"
    assert manifest["target"] == {
        "kind": "stub",
        "target_id": "stub_target",
        "app_version": "1.4.2",
    }


def test_a_target_without_a_prompt_still_gets_a_stable_identity_hash(tmp_path: Path) -> None:
    """`prompt_sha256` is the run's target identity; a change to it invalidates a baseline."""
    first = run_goldens(
        goldens_path=REAL_GOLDENS, out_dir=tmp_path / "a", samples=1, target=StubTarget()
    )
    second = run_goldens(
        goldens_path=REAL_GOLDENS, out_dir=tmp_path / "b", samples=1, target=StubTarget()
    )

    digest = read_manifest(first.out_dir)["prompt_sha256"]

    assert len(digest) == 64
    assert digest == read_manifest(second.out_dir)["prompt_sha256"]
    assert read_manifest(first.out_dir)["model_id"] == "stub_target"


def test_a_failing_target_is_recorded_as_a_failed_sample(tmp_path: Path) -> None:
    summary = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "run",
        samples=1,
        target=StubTarget(fail_on="Brightloom"),
    )

    rows = [row for row in read_jsonl(summary.out_dir / "outputs.jsonl") if row["error"]]

    assert summary.failed == 1
    assert rows[0]["error_type"] == "TargetExecutionError"
    assert rows[0]["output"] is None


def test_run_goldens_needs_either_a_provider_or_a_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="target"):
        run_goldens(goldens_path=REAL_GOLDENS, out_dir=tmp_path / "run", samples=1)


# --- the builtin path is unchanged ------------------------------------------


def test_the_builtin_manifest_keeps_the_keys_the_baseline_was_built_on(
    tmp_path: Path,
) -> None:
    provider = FakeProvider("A short summary.")

    from_provider = run_goldens(
        goldens_path=REAL_GOLDENS, out_dir=tmp_path / "a", samples=1, provider=provider
    )
    from_target = run_goldens(
        goldens_path=REAL_GOLDENS,
        out_dir=tmp_path / "b",
        samples=1,
        target=BuiltinSummarizerTarget(FakeProvider("A short summary.")),
    )

    old, new = read_manifest(from_provider.out_dir), read_manifest(from_target.out_dir)

    assert old["prompt_sha256"] == new["prompt_sha256"]
    assert old["model_id"] == new["model_id"] == "fake-provider"
    assert old["provider_class"] == new["provider_class"] == "FakeProvider"
    assert old["target"]["kind"] == "builtin"


# --- selecting a target from a config file ----------------------------------


def test_without_a_config_the_target_is_the_builtin_summarizer() -> None:
    target = build_target(config_path=None, dry_run=True)

    assert isinstance(target, BuiltinSummarizerTarget)
    assert target.provenance()["model_id"] == "dry-run-fake"


def test_the_committed_config_still_selects_the_builtin_summarizer() -> None:
    target = build_target(config_path=REAL_CONFIG, dry_run=True)

    assert isinstance(target, BuiltinSummarizerTarget)


def test_a_config_can_point_stage_01_at_an_external_app(tmp_path: Path) -> None:
    config = tmp_path / "regression.external.toml"
    config.write_text(
        EXTERNAL_CONFIG.format(argv=json.dumps([sys.executable, str(FIXTURE_APP)])),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    exit_code = main(
        [
            "--goldens",
            str(REAL_GOLDENS),
            "--config",
            str(config),
            "--runs-dir",
            str(runs_dir),
            "--samples",
            "1",
        ]
    )

    assert exit_code == 0
    run_dir = next(iter(runs_dir.iterdir()))
    rows = read_jsonl(run_dir / "outputs.jsonl")
    assert len(rows) == CASE_COUNT
    assert all(row["output"] == row["output"].upper() for row in rows)
    manifest = read_manifest(run_dir)
    assert manifest["target"]["kind"] == "command"
    assert manifest["target_id"].startswith("command:")


def test_a_config_whose_target_section_is_broken_exits_two(tmp_path: Path) -> None:
    config = tmp_path / "regression.broken.toml"
    config.write_text(
        EXTERNAL_CONFIG.format(argv='"python app.py"'), encoding="utf-8"
    )

    exit_code = main(
        [
            "--goldens",
            str(REAL_GOLDENS),
            "--config",
            str(config),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--dry-run",
        ]
    )

    assert exit_code == 2
