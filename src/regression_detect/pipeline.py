"""One command that runs the whole detector: run, judge, compare.

Stages 01, 02 and 03 are separate on purpose — each has its own contract, its own
artifacts and its own exit code — but a pull request wants one answer, not three
commands. This module calls the three stages' own functions in order, in one
process. It is not a shell wrapper: nothing here shells out, so a failure inside
a stage arrives as that stage's typed error rather than as an exit status to be
guessed at.

The baseline is read *before* stage 01 runs. A missing or malformed baseline
should cost nothing, and finding out after 67 paid model calls is finding out
too late.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from .baseline import BaselineInputError, build_baseline, read_baseline
from .compare import ComparisonResult, compare
from .compare_run import render_report, write_comparison
from .comparison import INPUT_ERROR_EXIT_CODE
from .config_file import DEFAULT_CONFIG_PATH, ConfigFileError, load_config
from .goldens import GoldenDatasetError
from .judge.criterion import DEFAULT_JUDGE_PROMPT_PATH
from .judge_inputs import JudgeRunError
from .judge_runner import JudgeRunSummary, judge_run
from .judge_runner import build_provider as build_judge_provider
from .providers.base import ProviderConfigError
from .report import write_report_for_run
from .report_inputs import ReportInputError
from .runner import (
    DEFAULT_GOLDENS_PATH,
    DEFAULT_RUNS_DIR,
    RunSummary,
    build_target,
    display_path,
    run_directory_name,
    run_goldens,
)
from .target.adapters.base import TargetConfigError
from .target.summarizer import DEFAULT_PROMPT_PATH


@dataclass(frozen=True)
class DetectOutcome:
    """What one end-to-end detection produced."""

    run_dir: Path
    run_summary: RunSummary
    judge_summary: JudgeRunSummary
    comparison: ComparisonResult
    comparison_path: Path
    report_path: Path

    @property
    def exit_code(self) -> int:
        """The comparison's verdict decides the process exit code."""
        return self.comparison.exit_code


def detect(
    *,
    baseline_path: Path,
    goldens_path: Path = DEFAULT_GOLDENS_PATH,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    judge_prompt_path: Path = DEFAULT_JUDGE_PROMPT_PATH,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    config_path: Path = DEFAULT_CONFIG_PATH,
    samples: int,
    min_interval_ms: int = 0,
    dry_run: bool = False,
) -> DetectOutcome:
    """Run stage 01, then stage 02, then stage 03, and return all three results.

    A dry run skips stage 03's comparability check, and records that it did. Its
    providers are canned, so its manifests record `dry-run-fake` rather than a
    model id; enforcing identity there would make exit 3 the normal outcome of
    the offline smoke test, and writing the baseline's model ids into a manifest
    no model produced would be worse — a run that names a model nobody called.

    Raises:
        ConfigFileError: if `regression.toml` is missing or invalid.
        BaselineInputError: if the baseline or the finished run cannot be read,
            or (as `ComparabilityError`) if the run did not measure what the
            baseline measured.
        GoldenDatasetError: if the dataset is missing or invalid.
        JudgeRunError: if the run directory stage 02 reads back is malformed.
        ProviderConfigError: if the provider cannot be built.
        TargetConfigError: if the `[target]` section does not describe a target.
        ReportInputError: if the finished run cannot be rendered into a report.
        ValueError: if `samples` or `min_interval_ms` is out of range.
        FileNotFoundError: if a prompt file is missing.
    """
    settings = load_config(config_path).compare
    baseline = read_baseline(baseline_path)

    run_dir = Path(runs_dir) / run_directory_name()
    run_summary = run_goldens(
        goldens_path=goldens_path,
        out_dir=run_dir,
        samples=samples,
        target=build_target(
            config_path=config_path, dry_run=dry_run, prompt_path=prompt_path
        ),
        prompt_path=prompt_path,
        min_interval_ms=min_interval_ms,
    )
    judge_summary = judge_run(
        run_dir=run_summary.out_dir,
        goldens_path=goldens_path,
        provider=build_judge_provider(dry_run=dry_run),
        prompt_path=judge_prompt_path,
        min_interval_ms=min_interval_ms,
    )

    candidate = build_baseline([run_summary.out_dir])
    result = compare(
        baseline,
        candidate,
        alpha=settings.alpha,
        min_effect=settings.min_effect,
        min_samples=settings.min_samples,
        max_judge_error_rate=settings.max_judge_error_rate,
        check_identity=not dry_run,
    )

    comparison_path = write_comparison(result, run_summary.out_dir, baseline=baseline)
    return DetectOutcome(
        run_dir=run_summary.out_dir,
        run_summary=run_summary,
        judge_summary=judge_summary,
        comparison=result,
        comparison_path=comparison_path,
        report_path=write_report_for_run(run_summary.out_dir),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="detect",
        description="Run the goldens, judge them, and compare against a baseline.",
    )
    parser.add_argument(
        "--baseline", type=Path, required=True, help="The committed baseline JSON to compare to."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=None,
        help="Samples per golden case (default: the [run] samples in regression.toml).",
    )
    parser.add_argument(
        "--goldens", type=Path, default=DEFAULT_GOLDENS_PATH, help="Path to the golden dataset."
    )
    parser.add_argument(
        "--prompt", type=Path, default=DEFAULT_PROMPT_PATH, help="Target system prompt to run."
    )
    parser.add_argument(
        "--judge-prompt",
        type=Path,
        default=DEFAULT_JUDGE_PROMPT_PATH,
        help="Judge system prompt to grade with.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Directory that holds timestamped run directories (default: runs).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Configuration file holding the thresholds (default: regression.toml).",
    )
    parser.add_argument(
        "--min-interval-ms",
        type=int,
        default=0,
        help=(
            "Minimum milliseconds between consecutive model calls, in both stage 01 "
            "and stage 02 (default: 0). Set this to 60000/RPM when the provider quota "
            "is per minute."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Call nothing: canned in-memory providers, and a canned in-memory target "
            "for any non-builtin kind the config names. No API key, no subprocess and "
            "no request, so no run reaches your app."
        ),
    )
    return parser


def _print_outcome(outcome: DetectOutcome) -> None:
    run = outcome.run_summary
    judge = outcome.judge_summary
    print(f"Run directory: {display_path(outcome.run_dir)}")
    print(f"  Stage 01: {run.total} call(s) — {run.ok} ok, {run.failed} failed")
    print(
        f"  Stage 02: {judge.verdicts_ok} verdict(s) ok, {judge.judge_errors} judge error(s), "
        f"{judge.skipped_outputs} output(s) skipped"
    )
    print(f"  Stage 03: {display_path(outcome.comparison_path)}")
    print(f"  Stage 04: {display_path(outcome.report_path)}")
    identity = outcome.comparison.identity
    if identity is not None and not identity.checked:
        print(
            "  Comparability: not checked — this was a dry run, so its model ids are "
            "placeholders and nothing asserts it measured what the baseline measured."
        )
    print()
    print(render_report(outcome.comparison))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the comparison's exit code, or 3 on a bad input."""
    args = build_parser().parse_args(argv)

    try:
        samples = args.samples
        if samples is None:
            samples = load_config(args.config).run.samples
        outcome = detect(
            baseline_path=args.baseline,
            goldens_path=args.goldens,
            prompt_path=args.prompt,
            judge_prompt_path=args.judge_prompt,
            runs_dir=args.runs_dir,
            config_path=args.config,
            samples=samples,
            min_interval_ms=args.min_interval_ms,
            dry_run=args.dry_run,
        )
    except (
        ConfigFileError,
        BaselineInputError,
        GoldenDatasetError,
        JudgeRunError,
        ProviderConfigError,
        TargetConfigError,
        ReportInputError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return INPUT_ERROR_EXIT_CODE

    _print_outcome(outcome)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
