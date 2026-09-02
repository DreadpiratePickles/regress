"""Read a judged run back in, and refuse to aggregate a broken one.

Stage 03's inputs are three files an earlier process wrote: stage 01's
`manifest.json`, stage 02's `judge_manifest.json`, and the `verdicts.jsonl`
between them. They get the same suspicion as any other boundary — every field
is checked before it reaches the arithmetic, and every violation is a typed
error naming the file and the line.

Stage 01's manifest reader is reused from `judge_inputs`; its errors are
re-raised as `BaselineInputError` so a caller of stage 03 only has to know about
one error type.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .judge_inputs import JudgeRunError, read_stage_01_manifest
from .judge_runner import JUDGE_MANIFEST_FILENAME, VERDICTS_FILENAME
from .runner import display_path


class BaselineInputError(Exception):
    """A run directory cannot be aggregated: missing, malformed, or not comparable."""


@dataclass(frozen=True)
class RunProvenance:
    """Everything about a judged run that has to match across a baseline."""

    run_id: str
    goldens_sha256: str
    prompt_sha256: str
    target_model_id: str
    judge_prompt_sha256: str
    judge_model_id: str


@dataclass(frozen=True)
class VerdictRecord:
    """One judged criterion, reduced to what the statistics need."""

    case_id: str
    criterion_index: int
    criterion: str
    passed: bool | None

    @property
    def key(self) -> tuple[str, int, str]:
        """Identity of the criterion: case, position and the text itself.

        The text is part of the identity on purpose. An edited criterion is a
        different question, and comparing a new question's answers against an
        old question's answers would look valid and mean nothing.
        """
        return (self.case_id, self.criterion_index, self.criterion)


def _string_field(source: dict[str, Any], key: str, *, where: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BaselineInputError(f"{where}: '{key}' must be a non-empty string")
    return value


def _read_json_object(path: Path, *, what: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BaselineInputError(f"{what} not found: {display_path(path)}") from exc
    except OSError as exc:
        raise BaselineInputError(f"{what} could not be read: {display_path(path)}") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BaselineInputError(
            f"{what} is not valid JSON: {display_path(path)} ({exc.msg})"
        ) from exc
    if not isinstance(document, dict):
        raise BaselineInputError(f"{what} is not a JSON object: {display_path(path)}")
    return document


def read_provenance(run_dir: Path) -> RunProvenance:
    """Read the fields two runs must agree on before they can be pooled.

    Raises:
        BaselineInputError: if either manifest is missing, malformed, or omits a
            field the comparison depends on.
    """
    run_dir = Path(run_dir)
    try:
        stage_01 = read_stage_01_manifest(run_dir)
    except JudgeRunError as exc:
        raise BaselineInputError(str(exc)) from exc

    judge_path = run_dir / JUDGE_MANIFEST_FILENAME
    stage_02 = _read_json_object(judge_path, what="Judge manifest")

    stage_01_where = f"{display_path(run_dir)}/manifest.json"
    judge_where = display_path(judge_path)
    return RunProvenance(
        run_id=run_dir.name,
        goldens_sha256=_string_field(stage_01, "goldens_sha256", where=stage_01_where),
        prompt_sha256=_string_field(stage_01, "prompt_sha256", where=stage_01_where),
        target_model_id=_string_field(stage_01, "model_id", where=stage_01_where),
        judge_prompt_sha256=_string_field(stage_02, "judge_prompt_sha256", where=judge_where),
        judge_model_id=_string_field(stage_02, "judge_model_id", where=judge_where),
    )


def _validated_verdict(entry: Any, *, where: str) -> VerdictRecord:
    if not isinstance(entry, dict):
        raise BaselineInputError(f"{where} is not a JSON object")

    criterion_index = entry.get("criterion_index")
    if isinstance(criterion_index, bool) or not isinstance(criterion_index, int):
        raise BaselineInputError(f"{where}: 'criterion_index' must be an integer")
    if criterion_index < 0:
        raise BaselineInputError(f"{where}: 'criterion_index' must not be negative")

    passed = entry.get("passed")
    if passed is not None and not isinstance(passed, bool):
        raise BaselineInputError(f"{where}: 'passed' must be true, false or null")

    return VerdictRecord(
        case_id=_string_field(entry, "case_id", where=where),
        criterion_index=criterion_index,
        criterion=_string_field(entry, "criterion", where=where),
        passed=passed,
    )


def read_verdicts(run_dir: Path) -> list[VerdictRecord]:
    """Read and validate every row of a run's `verdicts.jsonl`.

    Raises:
        BaselineInputError: if the file is missing or unreadable, if any line is
            not a JSON object carrying the fields stage 03 depends on, or if the
            run holds no verdict at all.
    """
    path = Path(run_dir) / VERDICTS_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BaselineInputError(f"Judge verdicts not found: {display_path(path)}") from exc
    except OSError as exc:
        raise BaselineInputError(f"Judge verdicts could not be read: {display_path(path)}") from exc

    records: list[VerdictRecord] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{display_path(path)} line {number}"
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BaselineInputError(f"{where} is not valid JSON: {exc.msg}") from exc
        records.append(_validated_verdict(entry, where=where))

    if not records:
        raise BaselineInputError(
            f"{display_path(path)} holds no verdict rows; the run was never judged."
        )
    return records
