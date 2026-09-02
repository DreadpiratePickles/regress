"""Read a completed stage-01 run back in, and refuse to judge a broken one.

Stage 02's inputs are files on disk written by an earlier process. They are
treated with the same suspicion as any other boundary: every row is validated
before the judge sees it, and every violation is a typed error naming the file
and the line.

The dataset check lives here too. Judging a run against a different `cases.yaml`
would grade outputs with criteria they were never produced for — the scores
would look valid and mean nothing — so a hash mismatch aborts.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runner import MANIFEST_FILENAME, OUTPUTS_FILENAME, display_path


class JudgeRunError(Exception):
    """The stage cannot run: a missing or malformed run directory, or bad inputs."""


class GoldensMismatchError(JudgeRunError):
    """The goldens given are not the goldens the run was produced from."""


@dataclass(frozen=True)
class OutputRow:
    """One validated row of stage 01's `outputs.jsonl`."""

    case_id: str
    sample_index: int
    output: str | None
    error_type: str | None = None
    error: str | None = None

    @property
    def judgeable(self) -> bool:
        return self.output is not None


def read_stage_01_manifest(run_dir: Path) -> dict[str, Any]:
    """Read `manifest.json` from a run directory.

    Raises:
        JudgeRunError: if it is missing, unreadable, not JSON, or not an object.
    """
    path = Path(run_dir) / MANIFEST_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise JudgeRunError(f"Stage 01 manifest not found: {display_path(path)}") from exc
    except OSError as exc:
        raise JudgeRunError(f"Stage 01 manifest could not be read: {display_path(path)}") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JudgeRunError(
            f"Stage 01 manifest is not valid JSON: {display_path(path)} ({exc.msg})"
        ) from exc
    if not isinstance(manifest, dict):
        raise JudgeRunError(f"Stage 01 manifest is not a JSON object: {display_path(path)}")
    return manifest


def _validated_row(entry: Any, *, where: str) -> OutputRow:
    if not isinstance(entry, dict):
        raise JudgeRunError(f"{where} is not a JSON object")

    case_id, sample_index, output = (
        entry.get("case_id"),
        entry.get("sample_index"),
        entry.get("output"),
    )
    if not isinstance(case_id, str) or not case_id:
        raise JudgeRunError(f"{where}: 'case_id' must be a non-empty string")
    if not isinstance(sample_index, int) or isinstance(sample_index, bool):
        raise JudgeRunError(f"{where}: 'sample_index' must be an integer")
    if output is not None and not isinstance(output, str):
        raise JudgeRunError(f"{where}: 'output' must be a string or null")

    return OutputRow(
        case_id=case_id,
        sample_index=sample_index,
        output=output,
        error_type=entry.get("error_type"),
        error=entry.get("error"),
    )


def read_output_rows(run_dir: Path) -> list[OutputRow]:
    """Read and validate every row of stage 01's `outputs.jsonl`.

    Raises:
        JudgeRunError: if the file is missing or unreadable, or any line is not
            a JSON object carrying the fields stage 02 depends on.
    """
    path = Path(run_dir) / OUTPUTS_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise JudgeRunError(f"Stage 01 outputs not found: {display_path(path)}") from exc
    except OSError as exc:
        raise JudgeRunError(f"Stage 01 outputs could not be read: {display_path(path)}") from exc

    rows: list[OutputRow] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{display_path(path)} line {number}"
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JudgeRunError(f"{where} is not valid JSON: {exc.msg}") from exc
        rows.append(_validated_row(entry, where=where))
    return rows


def check_goldens_match(*, manifest: dict[str, Any], goldens_path: Path, digest: str) -> str:
    """Confirm the dataset hash matches the one recorded in the run's manifest.

    Returns:
        The agreed sha256, for the judge manifest to record in turn.

    Raises:
        JudgeRunError: if the manifest records no dataset hash.
        GoldensMismatchError: if the hashes differ.
    """
    expected = manifest.get("goldens_sha256")
    if not isinstance(expected, str) or not expected:
        raise JudgeRunError("Stage 01 manifest has no 'goldens_sha256'; the run is unusable.")
    if digest != expected:
        raise GoldensMismatchError(
            f"{display_path(goldens_path)} (sha256 {digest[:12]}…) is not the dataset this "
            f"run was produced from (sha256 {expected[:12]}…). Judge the run against the "
            "dataset recorded in its manifest, or re-run stage 01."
        )
    return expected
