"""Stage 02: grade a completed stage-01 run, one criterion at a time.

This stage reads a run directory and writes four more files back into it. It
never re-runs the target feature and never edits what stage 01 recorded.

One model call per (output, criterion, judge sample) is the only non-deterministic
step. Loading, hashing, timing, counting and scoring are deterministic code.

A judge call that fails — a provider error, or a reply that will not parse — is
recorded with its error type and the run continues. It is never silently turned
into a failed criterion: `passed` stays null so the difference survives.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .goldens import GoldenCase, GoldenDatasetError, goldens_sha256, load_goldens
from .judge.config import judge_model_id
from .judge.criterion import (
    DEFAULT_JUDGE_PROMPT_PATH,
    JudgeError,
    judge_criterion,
    judge_prompt_sha256,
)
from .judge_inputs import (
    GoldensMismatchError,
    JudgeRunError,
    OutputRow,
    check_goldens_match,
    read_output_rows,
    read_stage_01_manifest,
)
from .pacing import pace, validate_interval
from .providers.base import Provider, ProviderConfigError, ProviderError
from .providers.fake import FakeProvider
from .runner import OUTPUTS_FILENAME, display_path, utc_stamp
from .scoring import VerdictRow, build_scores, render_judged

DEFAULT_GOLDENS_PATH = Path("goldens") / "cases.yaml"

VERDICTS_FILENAME = "verdicts.jsonl"
JUDGE_MANIFEST_FILENAME = "judge_manifest.json"
SCORES_FILENAME = "scores.json"
JUDGED_FILENAME = "judged.md"

DRY_RUN_VERDICT = json.dumps(
    {
        "reason": "Placeholder verdict produced by a dry run; no judge was called.",
        "passed": True,
    }
)

__all__ = [
    "DRY_RUN_VERDICT",
    "GoldensMismatchError",
    "JUDGED_FILENAME",
    "JUDGE_MANIFEST_FILENAME",
    "JudgeRunError",
    "JudgeRunSummary",
    "SCORES_FILENAME",
    "VERDICTS_FILENAME",
    "build_parser",
    "judge_run",
    "main",
]
"""The stage's public surface. The two errors are re-exported from
`judge_inputs` so a caller only has to know about the stage, not its readers."""


@dataclass(frozen=True)
class JudgeRunSummary:
    """What a completed stage-02 run produced."""

    run_dir: Path
    verdicts_ok: int
    judge_errors: int
    skipped_outputs: int
    scores: dict[str, Any]

    @property
    def has_failures(self) -> bool:
        return self.judge_errors > 0


def _judge_one(
    *,
    case: GoldenCase,
    output: OutputRow,
    criterion_index: int,
    judge_sample_index: int,
    provider: Provider,
    prompt_path: Path,
    prompt_hash: str,
    temperature: float,
) -> VerdictRow:
    """Judge one criterion once. Typed failures become a recorded row, not a crash."""
    criterion = case.criteria[criterion_index]
    started = time.perf_counter()
    common = {
        "case_id": case.id,
        "sample_index": output.sample_index,
        "criterion_index": criterion_index,
        "criterion": criterion,
        "judge_sample_index": judge_sample_index,
        "judge_model_id": provider.model_id,
        "judge_prompt_sha256": prompt_hash,
    }
    try:
        verdict = judge_criterion(
            ticket=case.input,
            summary=output.output or "",
            criterion=criterion,
            provider=provider,
            prompt_path=prompt_path,
            temperature=temperature,
        )
    except (ProviderError, JudgeError) as exc:
        return VerdictRow(
            **common,
            passed=None,
            reason=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return VerdictRow(
        **common,
        passed=verdict.passed,
        reason=verdict.reason,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def judge_run(
    *,
    run_dir: Path,
    goldens_path: Path,
    provider: Provider,
    judge_samples: int = 1,
    prompt_path: Path = DEFAULT_JUDGE_PROMPT_PATH,
    temperature: float = 0.0,
    min_interval_ms: int = 0,
) -> JudgeRunSummary:
    """Grade every recorded output in `run_dir` and write the stage-02 artifacts.

    Raises:
        ValueError: if `judge_samples` is not a positive integer, or
            `min_interval_ms` is negative.
        JudgeRunError: if the run directory is missing or malformed, or an output
            row names a case the dataset does not contain.
        GoldensMismatchError: if the dataset does not match the run's manifest.
        GoldenDatasetError: if the dataset itself is invalid.
        FileNotFoundError: if the judge prompt file is missing.
    """
    if not isinstance(judge_samples, int) or judge_samples < 1:
        raise ValueError(f"judge_samples must be a positive integer, got {judge_samples!r}")
    validate_interval(min_interval_ms)

    run_dir, goldens_path, prompt_path = Path(run_dir), Path(goldens_path), Path(prompt_path)
    stage_01_manifest = read_stage_01_manifest(run_dir)
    outputs = read_output_rows(run_dir)
    cases = load_goldens(goldens_path)
    digest = check_goldens_match(
        manifest=stage_01_manifest, goldens_path=goldens_path, digest=goldens_sha256(goldens_path)
    )

    by_id = {case.id: case for case in cases}
    unknown = sorted({row.case_id for row in outputs} - set(by_id))
    if unknown:
        raise JudgeRunError(
            f"{OUTPUTS_FILENAME} names case(s) absent from {display_path(goldens_path)}: "
            f"{', '.join(unknown)}"
        )

    prompt_hash = judge_prompt_sha256(prompt_path)
    started_at = utc_stamp()

    verdicts: list[VerdictRow] = []
    skipped = 0
    previous_start: float | None = None
    for output in outputs:
        if not output.judgeable:
            skipped += 1
            continue
        case = by_id[output.case_id]
        for criterion_index in range(len(case.criteria)):
            for judge_sample_index in range(judge_samples):
                previous_start = pace(previous_start, min_interval_ms)
                verdicts.append(
                    _judge_one(
                        case=case,
                        output=output,
                        criterion_index=criterion_index,
                        judge_sample_index=judge_sample_index,
                        provider=provider,
                        prompt_path=prompt_path,
                        prompt_hash=prompt_hash,
                        temperature=temperature,
                    )
                )

    judge_errors = sum(1 for verdict in verdicts if not verdict.ok)
    manifest: dict[str, Any] = {
        "run_id": run_dir.name,
        "stage": "02_judge",
        "started_at_utc": started_at,
        "finished_at_utc": utc_stamp(),
        "goldens_path": display_path(goldens_path),
        "goldens_sha256": digest,
        "judge_prompt_path": display_path(prompt_path),
        "judge_prompt_sha256": prompt_hash,
        "judge_model_id": provider.model_id,
        "provider_class": type(provider).__name__,
        "temperature": temperature,
        "judge_samples": judge_samples,
        "counts": {
            "verdicts_ok": len(verdicts) - judge_errors,
            "judge_errors": judge_errors,
            "skipped_outputs": skipped,
        },
    }
    scores = build_scores(run_id=run_dir.name, cases=cases, verdicts=verdicts)

    (run_dir / VERDICTS_FILENAME).write_text(
        "".join(
            json.dumps(verdict.to_json_row(), ensure_ascii=False) + "\n" for verdict in verdicts
        ),
        encoding="utf-8",
    )
    (run_dir / JUDGE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / SCORES_FILENAME).write_text(
        json.dumps(scores, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / JUDGED_FILENAME).write_text(
        render_judged(
            cases=cases, outputs=outputs, verdicts=verdicts, scores=scores, manifest=manifest
        ),
        encoding="utf-8",
    )

    return JudgeRunSummary(
        run_dir=run_dir,
        verdicts_ok=len(verdicts) - judge_errors,
        judge_errors=judge_errors,
        skipped_outputs=skipped,
        scores=scores,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="judge_run",
        description="Stage 02: grade a stage-01 run, one criterion per judge call.",
    )
    parser.add_argument(
        "--run", type=Path, required=True, help="The stage-01 run directory to judge."
    )
    parser.add_argument(
        "--goldens",
        type=Path,
        default=DEFAULT_GOLDENS_PATH,
        help="Golden dataset; must be the one the run was produced from.",
    )
    parser.add_argument(
        "--judge-samples",
        type=int,
        default=1,
        help="How many times to judge each criterion (default: 1).",
    )
    parser.add_argument(
        "--prompt", type=Path, default=DEFAULT_JUDGE_PROMPT_PATH, help="Judge system prompt file."
    )
    parser.add_argument(
        "--temperature", type=float, default=0.0, help="Judge sampling temperature (default: 0.0)."
    )
    parser.add_argument(
        "--min-interval-ms",
        type=int,
        default=0,
        help=(
            "Minimum milliseconds between consecutive judge calls (default: 0). "
            "Set this to 60000/RPM when the provider quota is per minute."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a canned in-memory judge instead of calling a model. No API key needed.",
    )
    return parser


def build_provider(*, dry_run: bool) -> Provider:
    if dry_run:
        return FakeProvider(DRY_RUN_VERDICT, model_id="dry-run-fake-judge")
    from .providers.gemini import gemini_provider_from_env

    return gemini_provider_from_env(judge_model_id())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    try:
        provider = build_provider(dry_run=args.dry_run)
        summary = judge_run(
            run_dir=args.run,
            goldens_path=args.goldens,
            provider=provider,
            judge_samples=args.judge_samples,
            prompt_path=args.prompt,
            temperature=args.temperature,
            min_interval_ms=args.min_interval_ms,
        )
    except (
        JudgeRunError,
        GoldenDatasetError,
        ProviderConfigError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    overall = summary.scores["overall"]
    rate = "not judged" if overall["pass_rate"] is None else f"{overall['pass_rate'] * 100:.1f}%"
    print(f"Run directory: {display_path(summary.run_dir)}")
    print(
        f"Verdicts: {summary.verdicts_ok} ok, {summary.judge_errors} judge error(s), "
        f"{summary.skipped_outputs} output(s) skipped"
    )
    print(f"Overall pass rate: {rate} ({overall['passed']}/{overall['criteria_total']} criteria)")
    print(f"Read by hand: {display_path(summary.run_dir / JUDGED_FILENAME)}")

    if summary.has_failures:
        print(
            f"{summary.judge_errors} judge call(s) failed; see the 'error' field in "
            f"{VERDICTS_FILENAME}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
