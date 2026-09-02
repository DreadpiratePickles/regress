"""Tests for the golden dataset loader.

The golden dataset is the ground truth the whole tool stands on, so the loader
validates it strictly and fails loudly rather than silently dropping cases.
"""

from pathlib import Path

import pytest

from regression_detect.goldens import GoldenCase, GoldenDatasetError, load_goldens

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GOLDENS = REPO_ROOT / "goldens" / "cases.yaml"


def write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_the_real_golden_dataset() -> None:
    cases = load_goldens(REAL_GOLDENS)

    assert len(cases) == 15
    assert all(isinstance(case, GoldenCase) for case in cases)
    assert cases[0].id == "double_charge_refund"
    assert "billing" in cases[0].tags
    assert cases[0].criteria
    assert len({case.id for case in cases}) == 15


def test_every_real_case_has_at_least_one_criterion() -> None:
    for case in load_goldens(REAL_GOLDENS):
        assert case.criteria, f"{case.id} has no criteria"
        assert all(criterion.strip() for criterion in case.criteria)


def test_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
- id: same_id
  tags: [a]
  input: one
  criteria: [Says something.]
- id: same_id
  tags: [b]
  input: two
  criteria: [Says something else.]
""",
    )

    with pytest.raises(GoldenDatasetError, match="duplicate"):
        load_goldens(path)


def test_rejects_a_case_with_a_missing_key(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
- id: no_criteria_key
  tags: [a]
  input: one
""",
    )

    with pytest.raises(GoldenDatasetError, match="criteria"):
        load_goldens(path)


def test_rejects_empty_criteria(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
- id: empty_criteria
  tags: [a]
  input: one
  criteria: []
""",
    )

    with pytest.raises(GoldenDatasetError, match="criteria"):
        load_goldens(path)


def test_rejects_a_non_string_criterion(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
- id: numeric_criterion
  tags: [a]
  input: one
  criteria: [3]
""",
    )

    with pytest.raises(GoldenDatasetError, match="criteria"):
        load_goldens(path)


def test_rejects_an_id_that_is_not_snake_case(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
- id: NotSnakeCase
  tags: [a]
  input: one
  criteria: [Says something.]
""",
    )

    with pytest.raises(GoldenDatasetError, match="snake_case"):
        load_goldens(path)


def test_rejects_a_top_level_mapping(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "cases: []\n")

    with pytest.raises(GoldenDatasetError, match="list"):
        load_goldens(path)


def test_rejects_an_empty_dataset(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "[]\n")

    with pytest.raises(GoldenDatasetError, match="empty"):
        load_goldens(path)


def test_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(GoldenDatasetError, match="not found"):
        load_goldens(tmp_path / "nope.yaml")


def test_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "- id: broken\n  criteria: [unclosed\n")

    with pytest.raises(GoldenDatasetError, match="parse"):
        load_goldens(path)


def test_notes_are_optional(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
- id: no_notes
  tags: [a]
  input: one
  criteria: [Says something.]
""",
    )

    (case,) = load_goldens(path)

    assert case.notes is None
