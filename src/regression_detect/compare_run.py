"""Stage 03's entry point: compare one judged run against the committed baseline.

`compare.py` stays pure, so every side effect lives here — reading the baseline
file, reading the candidate run, writing `comparison.json` back into that run
directory, and printing the explanation.

The exit code is the verdict:

    0  NO_REGRESSION   ship it
    1  REGRESSION      the drop survived the noise test
    2  INCONCLUSIVE    not enough judged evidence to say either way
    3  input error     the tool could not run; not a statement about quality

Codes 1 and 3 are kept apart deliberately: CI must be able to tell "the feature
got worse" from "the baseline file was missing".
"""

import argparse
import json
import sys
from pathlib import Path

from .baseline import BaselineInputError, build_baseline, read_baseline
from .compare import COMPARISON_FILENAME, ComparisonResult, compare
from .comparison import INPUT_ERROR_EXIT_CODE, percent
from .config_file import DEFAULT_CONFIG_PATH, ConfigFileError, load_config
from .runner import display_path


def write_comparison(result: ComparisonResult, run_dir: Path) -> Path:
    """Write `comparison.json` into the candidate's own run directory."""
    destination = Path(run_dir) / COMPARISON_FILENAME
    destination.write_text(
        json.dumps(result.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return destination


def render_report(result: ComparisonResult) -> str:
    """The terminal report: the explanation, then the rows that moved down."""
    lines = [
        f"Verdict: {result.verdict.value}",
        "",
        result.explain(),
        "",
        f"  Baseline   {percent(result.baseline_rate)} "
        f"({result.baseline_passes}/{result.baseline_n})  "
        f"95% CI {percent(result.baseline_interval[0])}–{percent(result.baseline_interval[1])}",
        f"  Candidate  {percent(result.candidate_rate)} "
        f"({result.candidate_passes}/{result.candidate_n})  "
        f"95% CI {percent(result.candidate_interval[0])}–{percent(result.candidate_interval[1])}",
        f"  p-value    {result.p_value:.4f}  (one-sided Fisher exact, alpha {result.alpha:g})",
        "",
    ]

    flagged = result.flagged
    if not flagged:
        lines.append("No criterion scored lower than the baseline.")
    else:
        lines.append(f"Criteria that fell ({len(flagged)}):")
        lines.append(f"  {'!':<1} {'case':<28} {'#':>2}  {'base':>7}  {'cand':>7}  criterion")
        for row in flagged:
            mark = "!" if row.hard_regression else " "
            lines.append(
                f"  {mark:<1} {row.case_id:<28} {row.criterion_index + 1:>2}  "
                f"{f'{row.baseline_passes}/{row.baseline_n}':>7}  "
                f"{f'{row.candidate_passes}/{row.candidate_n}':>7}  {row.criterion}"
            )
        lines.append("  ! marks a hard regression: always passed before, always fails now.")

    if result.unmatched:
        lines.extend(
            [
                "",
                f"Unmatched criteria ({len(result.unmatched)}) — the goldens changed, so these "
                "are in no number above:",
            ]
        )
        for item in result.unmatched:
            lines.append(
                f"    only in {item.present_in:<9} {item.case_id} "
                f"[{item.criterion_index + 1}] {item.criterion}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare",
        description="Stage 03: compare a judged run against the committed baseline.",
    )
    parser.add_argument(
        "--baseline", type=Path, required=True, help="The committed baseline JSON to compare to."
    )
    parser.add_argument(
        "--candidate", type=Path, required=True, help="A judged run directory to compare."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Configuration file holding the thresholds (default: regression.toml).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the verdict's exit code, or 3 on a bad input."""
    args = build_parser().parse_args(argv)

    try:
        settings = load_config(args.config).compare
        baseline = read_baseline(args.baseline)
        candidate = build_baseline([args.candidate])
        result = compare(
            baseline,
            candidate,
            alpha=settings.alpha,
            min_effect=settings.min_effect,
            min_samples=settings.min_samples,
            max_judge_error_rate=settings.max_judge_error_rate,
        )
    except (ConfigFileError, BaselineInputError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return INPUT_ERROR_EXIT_CODE

    destination = write_comparison(result, args.candidate)
    print(render_report(result))
    print(f"\nWritten: {display_path(destination)}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
