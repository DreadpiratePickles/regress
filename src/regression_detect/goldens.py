"""Load and validate the golden dataset.

The goldens are the ground truth the whole tool stands on. A malformed case that
loads quietly would silently shrink the regression net, so every violation is a
hard error naming the offending case.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
REQUIRED_KEYS = ("id", "tags", "input", "criteria")
OPTIONAL_KEYS = ("notes",)


class GoldenDatasetError(Exception):
    """The golden dataset is missing, unparseable, or violates the case rules."""


@dataclass(frozen=True)
class GoldenCase:
    """One golden case: an input plus the criteria any acceptable output must meet."""

    id: str
    tags: tuple[str, ...]
    input: str
    criteria: tuple[str, ...]
    notes: str | None = None


def goldens_sha256(path: Path) -> str:
    """Hash of the dataset file, recorded in run manifests to pin the version."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_yaml(path: Path) -> Any:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise GoldenDatasetError(f"Golden dataset not found: {path}") from exc
    except OSError as exc:
        raise GoldenDatasetError(f"Golden dataset could not be read: {path}") from exc

    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise GoldenDatasetError(f"Golden dataset failed to parse as YAML: {path}") from exc


def _validate_string_list(
    value: Any, *, field: str, case_id: str, allow_empty: bool
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GoldenDatasetError(
            f"Case '{case_id}': '{field}' must be a list, got {type(value).__name__}"
        )
    if not allow_empty and not value:
        raise GoldenDatasetError(f"Case '{case_id}': '{field}' must not be empty")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise GoldenDatasetError(
                f"Case '{case_id}': every entry in '{field}' must be a non-empty string"
            )
    return tuple(item.strip() for item in value)


def _build_case(entry: Any, position: int) -> GoldenCase:
    if not isinstance(entry, dict):
        raise GoldenDatasetError(
            f"Case at position {position}: expected a mapping, got {type(entry).__name__}"
        )

    raw_id = entry.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise GoldenDatasetError(f"Case at position {position}: 'id' must be a non-empty string")
    case_id = raw_id.strip()
    if not ID_PATTERN.match(case_id):
        raise GoldenDatasetError(
            f"Case '{case_id}': 'id' must be snake_case (lowercase letters, digits, underscores)"
        )

    missing = [key for key in REQUIRED_KEYS if key not in entry]
    if missing:
        raise GoldenDatasetError(f"Case '{case_id}': missing required key(s): {', '.join(missing)}")

    unknown = set(entry) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
    if unknown:
        raise GoldenDatasetError(
            f"Case '{case_id}': unknown key(s): {', '.join(sorted(unknown))}"
        )

    ticket = entry["input"]
    if not isinstance(ticket, str) or not ticket.strip():
        raise GoldenDatasetError(f"Case '{case_id}': 'input' must be a non-empty string")

    notes = entry.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise GoldenDatasetError(f"Case '{case_id}': 'notes' must be a string when present")

    return GoldenCase(
        id=case_id,
        tags=_validate_string_list(entry["tags"], field="tags", case_id=case_id, allow_empty=True),
        input=ticket,
        criteria=_validate_string_list(
            entry["criteria"], field="criteria", case_id=case_id, allow_empty=False
        ),
        notes=notes.strip() if isinstance(notes, str) else None,
    )


def load_goldens(path: Path) -> list[GoldenCase]:
    """Read the golden dataset and validate every case.

    Raises:
        GoldenDatasetError: on a missing file, a YAML parse failure, a non-list
            root, an empty dataset, a missing or malformed key, a non-snake_case
            id, a duplicate id, or empty criteria.
    """
    document = _read_yaml(Path(path))

    if not isinstance(document, list):
        raise GoldenDatasetError(
            f"Golden dataset must be a list of cases, got {type(document).__name__}: {path}"
        )
    if not document:
        raise GoldenDatasetError(f"Golden dataset is empty: {path}")

    cases = [_build_case(entry, position) for position, entry in enumerate(document)]

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise GoldenDatasetError(f"Golden dataset contains a duplicate id: '{case.id}'")
        seen.add(case.id)

    return cases
