"""Read a compared run back in, and refuse to report on a broken one.

Stage 04's inputs are six files an earlier process wrote into one run directory:
`comparison.json`, `manifest.json`, `judge_manifest.json`, `scores.json`,
`outputs.jsonl` and `verdicts.jsonl`. They get the same suspicion as any other
boundary — every field the report prints is checked before it is printed, and
every violation is a typed error naming the file.

The baseline is not re-read from disk. `comparison.json` records which baseline
the verdict was measured against, and that recorded identity is what the report
must describe: a baseline file can have moved on since, and a report that named
the current file while showing yesterday's numbers would be a lie with a
citation.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .comparison import COMPARISON_FILENAME, SCHEMA_VERSION
from .judge_inputs import JudgeRunError, read_output_rows, read_stage_01_manifest
from .judge_runner import JUDGE_MANIFEST_FILENAME, SCORES_FILENAME, VERDICTS_FILENAME
from .runner import display_path


class ReportInputError(Exception):
    """A run directory cannot be reported on: missing, malformed, or incomplete."""


@dataclass(frozen=True)
class Provenance:
    """What this run measured, and what baseline it was measured against."""

    run_id: str
    target_model_id: str
    judge_model_id: str
    prompt_sha256: str
    judge_prompt_sha256: str
    goldens_sha256: str
    samples: int
    baseline_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class CriterionEvidence:
    """What the judge was looking at, and what it said, for one criterion."""

    case_id: str
    criterion_index: int
    criterion: str
    outputs: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def key(self) -> tuple[str, int]:
        return (self.case_id, self.criterion_index)


@dataclass(frozen=True)
class ReportData:
    """Everything the report is rendered from, already read and validated."""

    comparison: dict[str, Any]
    provenance: Provenance
    scores_overall: dict[str, Any]
    evidence: tuple[CriterionEvidence, ...]

    def evidence_for(self, case_id: str, criterion_index: int) -> CriterionEvidence | None:
        for item in self.evidence:
            if item.key == (case_id, criterion_index):
                return item
        return None


REQUIRED_COMPARISON_KEYS = (
    "verdict",
    "explanation",
    "overall",
    "p_value",
    "thresholds",
    "candidate_judge_errors",
    "criteria",
    "unmatched",
)
REQUIRED_SCORES_KEYS = ("criteria_total", "passed", "failed", "errored")


def _read_json_object(path: Path, *, what: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ReportInputError(f"{what} not found: {display_path(path)}") from exc
    except OSError as exc:
        raise ReportInputError(f"{what} could not be read: {display_path(path)}") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportInputError(
            f"{what} is not valid JSON: {display_path(path)} ({exc.msg})"
        ) from exc
    if not isinstance(document, dict):
        raise ReportInputError(f"{what} is not a JSON object: {display_path(path)}")
    return document


def _string(source: dict[str, Any], key: str, *, where: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReportInputError(f"{where}: '{key}' must be a non-empty string")
    return value


def _positive_int(source: dict[str, Any], key: str, *, where: str) -> int:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReportInputError(f"{where}: '{key}' must be a positive integer")
    return value


def read_comparison(run_dir: Path) -> dict[str, Any]:
    """Read `comparison.json` and check it is the schema this report renders.

    Raises:
        ReportInputError: if the file is missing, malformed, of another schema
            version, or omits a field the report prints.
    """
    path = Path(run_dir) / COMPARISON_FILENAME
    document = _read_json_object(path, what="Comparison")
    where = display_path(path)

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ReportInputError(
            f"{where}: schema_version is {version!r}, but this report renders version "
            f"{SCHEMA_VERSION}. Re-run the comparison rather than guessing at the shape."
        )
    missing = [key for key in REQUIRED_COMPARISON_KEYS if key not in document]
    if missing:
        raise ReportInputError(f"{where}: missing key(s): {', '.join(missing)}")
    if document["verdict"] not in {"REGRESSION", "NO_REGRESSION", "INCONCLUSIVE"}:
        raise ReportInputError(f"{where}: unknown verdict {document['verdict']!r}")
    return document


def _baseline_run_ids(comparison: dict[str, Any], *, where: str) -> tuple[str, ...]:
    source = comparison.get("baseline_source")
    if source is None:
        return ()
    if not isinstance(source, dict):
        raise ReportInputError(f"{where}: 'baseline_source' must be a JSON object")
    run_ids = source.get("run_ids", [])
    if not isinstance(run_ids, list) or any(not isinstance(item, str) for item in run_ids):
        raise ReportInputError(f"{where}: 'baseline_source.run_ids' must be a list of strings")
    return tuple(run_ids)


def read_provenance(run_dir: Path, comparison: dict[str, Any]) -> Provenance:
    """Read the run's identity from both manifests and the recorded baseline.

    Raises:
        ReportInputError: if either manifest is missing, malformed, or omits a
            field the footer prints.
    """
    run_dir = Path(run_dir)
    try:
        stage_01 = read_stage_01_manifest(run_dir)
    except JudgeRunError as exc:
        raise ReportInputError(str(exc)) from exc

    judge_path = run_dir / JUDGE_MANIFEST_FILENAME
    stage_02 = _read_json_object(judge_path, what="Judge manifest")

    stage_01_where = f"{display_path(run_dir)}/manifest.json"
    judge_where = display_path(judge_path)
    comparison_where = f"{display_path(run_dir)}/{COMPARISON_FILENAME}"
    return Provenance(
        run_id=run_dir.name,
        target_model_id=_string(stage_01, "model_id", where=stage_01_where),
        judge_model_id=_string(stage_02, "judge_model_id", where=judge_where),
        prompt_sha256=_string(stage_01, "prompt_sha256", where=stage_01_where),
        judge_prompt_sha256=_string(stage_02, "judge_prompt_sha256", where=judge_where),
        goldens_sha256=_string(stage_01, "goldens_sha256", where=stage_01_where),
        samples=_positive_int(stage_01, "samples", where=stage_01_where),
        baseline_run_ids=_baseline_run_ids(comparison, where=comparison_where),
    )


def read_scores_overall(run_dir: Path) -> dict[str, Any]:
    """Read the whole-run tally stage 02 counted.

    Raises:
        ReportInputError: if `scores.json` is missing, malformed, or has no
            `overall` block with the counts the report prints.
    """
    path = Path(run_dir) / SCORES_FILENAME
    document = _read_json_object(path, what="Scores")
    overall = document.get("overall")
    if not isinstance(overall, dict):
        raise ReportInputError(f"{display_path(path)}: 'overall' must be a JSON object")
    missing = [key for key in REQUIRED_SCORES_KEYS if key not in overall]
    if missing:
        raise ReportInputError(f"{display_path(path)}: 'overall' is missing {', '.join(missing)}")
    return overall


def _read_verdict_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / VERDICTS_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ReportInputError(f"Judge verdicts not found: {display_path(path)}") from exc
    except OSError as exc:
        raise ReportInputError(f"Judge verdicts could not be read: {display_path(path)}") from exc

    rows: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{display_path(path)} line {number}"
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportInputError(f"{where} is not valid JSON: {exc.msg}") from exc
        if not isinstance(entry, dict):
            raise ReportInputError(f"{where} is not a JSON object")
        rows.append(entry)
    return rows


def read_evidence(run_dir: Path) -> tuple[CriterionEvidence, ...]:
    """Collect, per criterion the judge failed, the outputs and the reasons given.

    Only failing verdicts are collected: the evidence section exists to show why
    a criterion fell, and a passing verdict explains nothing about a drop.

    Raises:
        ReportInputError: if `outputs.jsonl` or `verdicts.jsonl` is missing or
            malformed.
    """
    run_dir = Path(run_dir)
    try:
        outputs = read_output_rows(run_dir)
    except JudgeRunError as exc:
        raise ReportInputError(str(exc)) from exc
    by_sample = {(row.case_id, row.sample_index): row.output for row in outputs}

    collected: dict[tuple[str, int], dict[str, Any]] = {}
    for row in _read_verdict_rows(run_dir):
        if row.get("passed") is not False:
            continue
        case_id, criterion_index = row.get("case_id"), row.get("criterion_index")
        if not isinstance(case_id, str) or not isinstance(criterion_index, int):
            continue
        key = (case_id, criterion_index)
        entry = collected.setdefault(
            key,
            {"criterion": str(row.get("criterion", "")), "outputs": [], "reasons": []},
        )
        output = by_sample.get((case_id, row.get("sample_index")))
        if isinstance(output, str) and output not in entry["outputs"]:
            entry["outputs"].append(output)
        reason = row.get("reason")
        if isinstance(reason, str) and reason.strip():
            entry["reasons"].append(reason)

    return tuple(
        CriterionEvidence(
            case_id=case_id,
            criterion_index=criterion_index,
            criterion=entry["criterion"],
            outputs=tuple(entry["outputs"]),
            reasons=tuple(entry["reasons"]),
        )
        for (case_id, criterion_index), entry in sorted(collected.items())
    )


def read_report_data(run_dir: Path) -> ReportData:
    """Read everything stage 04 renders from one compared run directory.

    Raises:
        ReportInputError: if any of the six files is missing or malformed.
    """
    run_dir = Path(run_dir)
    comparison = read_comparison(run_dir)
    return ReportData(
        comparison=comparison,
        provenance=read_provenance(run_dir, comparison),
        scores_overall=read_scores_overall(run_dir),
        evidence=read_evidence(run_dir),
    )
