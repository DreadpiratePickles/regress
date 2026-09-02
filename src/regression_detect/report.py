"""Stage 04: turn a finished comparison into the Markdown a human reads on a PR.

Nothing here decides anything. Stage 03 already produced the verdict, the
p-value and the sentence that justifies them; this module arranges them, plus
the judge's own words about the criteria that fell, into one document that fits
in a pull request comment.

`render_report` is a pure function of a `ReportData`: the same inputs always
produce the same Markdown, which is what makes the document reviewable and what
lets the tests build one without a run directory. Reading a run off disk lives
in `report_inputs.py`; writing the file lives at the bottom of this module.

No absolute path is ever written into the report. It is a shared artifact, and a
developer's home directory is not part of the evidence.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from .comparison import INPUT_ERROR_EXIT_CODE, percent
from .report_inputs import (
    CriterionEvidence,
    Provenance,
    ReportData,
    ReportInputError,
    read_report_data,
)
from .review import fence_text
from .runner import display_path

REPORT_FILENAME = "report.md"

__all__ = [
    "BADGES",
    "REPORT_FILENAME",
    "CriterionEvidence",
    "Provenance",
    "ReportData",
    "build_parser",
    "main",
    "render_report",
    "write_report",
    "write_report_for_run",
]
"""The stage's public surface. The three records are re-exported from
`report_inputs` so a caller only has to know about the stage, not its readers."""

BADGES = {
    "REGRESSION": "🔴 REGRESSION",
    "NO_REGRESSION": "🟢 NO_REGRESSION",
    "INCONCLUSIVE": "🟡 INCONCLUSIVE",
}
HARD_MARK = "‼️"
SHORT_HASH_CHARS = 12


def _short(digest: str) -> str:
    return f"{digest[:SHORT_HASH_CHARS]}…"


def _points(fraction: float) -> str:
    return f"{fraction * 100:.1f}"


def _threshold_points(fraction: float) -> str:
    """A threshold reads better without trailing zeros: 5, not 5.0."""
    return f"{fraction * 100:g}"


def _count_and_rate(side: dict[str, Any]) -> str:
    n = side["n"]
    rate = (side["passes"] / n) if n else None
    return f"{side['passes']}/{n} ({percent(rate)})"


def _movement(row: dict[str, Any]) -> float | None:
    """Candidate minus baseline for one criterion; `None` when a side is empty."""
    difference = row.get("difference")
    return None if difference is None else float(difference)


def _title(data: ReportData) -> list[str]:
    comparison = data.comparison
    verdict = comparison["verdict"]
    return [
        f"## Regression check — `{data.provenance.run_id}`",
        "",
        f"**{BADGES[verdict]}**",
        "",
        comparison["explanation"],
        "",
    ]


def _overall(data: ReportData) -> list[str]:
    comparison = data.comparison
    overall = comparison["overall"]
    baseline, candidate = overall["baseline"], overall["candidate"]
    thresholds = comparison["thresholds"]
    errors = comparison["candidate_judge_errors"]
    scores = data.scores_overall

    return [
        "### Overall",
        "",
        "| Measure | Baseline | Candidate |",
        "|---|---:|---:|",
        f"| Passes / n | {baseline['passes']} / {baseline['n']} "
        f"| {candidate['passes']} / {candidate['n']} |",
        f"| Pass rate | {percent(baseline['rate'])} | {percent(candidate['rate'])} |",
        f"| 95% Wilson CI | {percent(baseline['wilson_95'][0])}–"
        f"{percent(baseline['wilson_95'][1])} | {percent(candidate['wilson_95'][0])}–"
        f"{percent(candidate['wilson_95'][1])} |",
        "",
        "| p-value (one-sided Fisher exact) | Minimum effect | Alpha |",
        "|---:|---:|---:|",
        f"| {comparison['p_value']:.4f} | "
        f"{_threshold_points(thresholds['min_effect'])} points | "
        f"{thresholds['alpha']:g} |",
        "",
        f"Candidate run: {scores['passed']} passed, {scores['failed']} failed, "
        f"{scores['errored']} not judged, across {scores['criteria_total']} criteria.",
        "",
        f"Judge errors: {errors['errors']} of {errors['rows']} candidate verdict rows "
        f"({_points(errors['rate'])}%).",
        "",
    ]


def _criterion_table(rows: list[dict[str, Any]], *, heading: str, movement: str) -> list[str]:
    lines = [
        f"### {heading} ({len(rows)})",
        "",
        f"| Case | # | Criterion | Baseline | Candidate | {movement} |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        difference = _movement(row) or 0.0
        mark = f" {HARD_MARK}" if row["hard_regression"] else ""
        lines.append(
            f"| `{row['case_id']}` | {row['criterion_index'] + 1} | {row['criterion']} "
            f"| {_count_and_rate(row['baseline'])} | {_count_and_rate(row['candidate'])} "
            f"| {_points(abs(difference))} points{mark} |"
        )
    lines.append("")
    return lines


def _worsened(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in comparison["criteria"]
        if row["hard_regression"] or ((_movement(row) or 0.0) < 0)
    ]
    return sorted(
        rows,
        key=lambda row: (_movement(row) or 0.0, row["case_id"], row["criterion_index"]),
    )


def _improved(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in comparison["criteria"] if (_movement(row) or 0.0) > 0]
    return sorted(
        rows,
        key=lambda row: (-(_movement(row) or 0.0), row["case_id"], row["criterion_index"]),
    )


def _criteria_sections(data: ReportData) -> list[str]:
    worsened = _worsened(data.comparison)
    improved = _improved(data.comparison)

    if not worsened:
        lines = ["No criterion scored lower than the baseline.", ""]
    else:
        lines = _criterion_table(worsened, heading="Criteria that got worse", movement="Drop")
        if any(row["hard_regression"] for row in worsened):
            lines.extend(
                [
                    f"{HARD_MARK} marks a hard regression: the baseline always passed this "
                    "criterion and the candidate always fails it.",
                    "",
                ]
            )
    if improved:
        lines.extend(
            _criterion_table(improved, heading="Criteria that improved", movement="Gain")
        )
    return lines


def _unmatched(comparison: dict[str, Any]) -> list[str]:
    unmatched = comparison["unmatched"]
    if not unmatched:
        return []
    lines = [
        f"### Unmatched criteria ({len(unmatched)})",
        "",
        "These appear on one side only — the goldens changed — so they are in no number "
        "above.",
        "",
    ]
    for item in unmatched:
        lines.append(
            f"- only in **{item['present_in']}** — `{item['case_id']}` "
            f"[{item['criterion_index'] + 1}] {item['criterion']}"
        )
    lines.append("")
    return lines


def _details_block(row: dict[str, Any], evidence: CriterionEvidence) -> list[str]:
    lines = [
        "<details>",
        f"<summary><code>{row['case_id']}</code> [{row['criterion_index'] + 1}] "
        f"{row['criterion']}</summary>",
        "",
    ]
    for output in evidence.outputs:
        lines.extend(["Candidate output:", "", fence_text(output), ""])
    if evidence.reasons:
        lines.append("Judge:")
        lines.append("")
        lines.extend(f"- {reason}" for reason in evidence.reasons)
        lines.append("")
    lines.extend(["</details>", ""])
    return lines


def _evidence_section(data: ReportData) -> list[str]:
    blocks: list[str] = []
    for row in _worsened(data.comparison):
        evidence = data.evidence_for(row["case_id"], row["criterion_index"])
        if evidence is None or not (evidence.outputs or evidence.reasons):
            continue
        blocks.extend(_details_block(row, evidence))
    if not blocks:
        return []
    return ["### What the judge saw", "", *blocks]


def _footer(data: ReportData) -> list[str]:
    provenance = data.provenance
    baseline = ", ".join(f"`{run_id}`" for run_id in provenance.baseline_run_ids) or "—"
    return [
        "---",
        "",
        f"Run `{provenance.run_id}` · samples {provenance.samples} · "
        f"target model `{provenance.target_model_id}` · "
        f"judge model `{provenance.judge_model_id}`",
        "",
        f"Prompt `{_short(provenance.prompt_sha256)}` · "
        f"judge prompt `{_short(provenance.judge_prompt_sha256)}` · "
        f"goldens `{_short(provenance.goldens_sha256)}` · "
        f"baseline runs {baseline}",
        "",
    ]


def render_report(data: ReportData) -> str:
    """Render the whole `report.md`. Pure: no file is read and none is written."""
    lines = [
        *_title(data),
        *_overall(data),
        *_criteria_sections(data),
        *_unmatched(data.comparison),
        *_evidence_section(data),
        *_footer(data),
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_report(data: ReportData, destination: Path) -> Path:
    """Write the rendered report to `destination`, creating its directory."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_report(data), encoding="utf-8")
    return destination


def write_report_for_run(run_dir: Path) -> Path:
    """Read a finished run and write `report.md` into it.

    Raises:
        ReportInputError: if the run is missing a file the report is built from.
    """
    run_dir = Path(run_dir)
    return write_report(read_report_data(run_dir), run_dir / REPORT_FILENAME)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report",
        description="Stage 04: render a compared run into the Markdown a PR comment carries.",
    )
    parser.add_argument(
        "--run", type=Path, required=True, help="A run directory holding comparison.json."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Where to write the report (default: <run>/{REPORT_FILENAME}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0, or 3 when the run cannot be read."""
    args = build_parser().parse_args(argv)
    destination = args.out or (Path(args.run) / REPORT_FILENAME)

    try:
        data = read_report_data(args.run)
    except ReportInputError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return INPUT_ERROR_EXIT_CODE

    write_report(data, destination)
    print(f"Written: {display_path(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
