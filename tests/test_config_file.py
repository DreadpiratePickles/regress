"""Tests for the repo-root `regression.toml` reader.

The thresholds that decide a build's fate are configuration, not literals buried
in a comparison function. That makes the config file a boundary, so it is
validated like one: every violation is a typed error naming the key.
"""

from pathlib import Path

import pytest

from regression_detect.config_file import (
    DEFAULT_CONFIG_PATH,
    ConfigFileError,
    load_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID = """
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
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "regression.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_config_loads():
    config = load_config(REPO_ROOT / DEFAULT_CONFIG_PATH)

    assert 0.0 < config.compare.alpha < 1.0
    assert 0.0 <= config.compare.min_effect < 1.0
    assert config.compare.min_samples >= 1
    assert config.run.samples >= 1
    assert config.models.target_model_id_env == "TARGET_MODEL_ID"
    assert config.models.judge_model_id_env == "JUDGE_MODEL_ID"


def test_the_committed_config_names_no_model_id():
    """Model identifiers live in the two config modules, never in the toml."""
    text = (REPO_ROOT / DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")
    assert "gemini" not in text.lower()


def test_a_valid_file_is_read_field_by_field(tmp_path):
    config = load_config(write_config(tmp_path, VALID))

    assert config.compare.alpha == 0.05
    assert config.compare.min_effect == 0.05
    assert config.compare.min_samples == 30
    assert config.compare.max_judge_error_rate == 0.2
    assert config.run.samples == 1


def test_an_integer_threshold_is_accepted_as_a_float(tmp_path):
    text = VALID.replace("min_effect = 0.05", "min_effect = 0")
    assert load_config(write_config(tmp_path, text)).compare.min_effect == 0.0


def test_a_missing_file_is_a_typed_error(tmp_path):
    with pytest.raises(ConfigFileError, match="not found"):
        load_config(tmp_path / "absent.toml")


def test_a_file_that_is_not_toml_is_a_typed_error(tmp_path):
    with pytest.raises(ConfigFileError, match="TOML"):
        load_config(write_config(tmp_path, "[compare\nalpha = "))


@pytest.mark.parametrize("section", ["compare", "run", "models"])
def test_a_missing_section_is_a_typed_error(tmp_path, section):
    text = "\n".join(
        block for block in VALID.strip().split("\n\n") if not block.startswith(f"[{section}]")
    )
    with pytest.raises(ConfigFileError, match=section):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    "key", ["alpha", "min_effect", "min_samples", "max_judge_error_rate"]
)
def test_a_missing_compare_key_is_a_typed_error(tmp_path, key):
    text = "\n".join(line for line in VALID.splitlines() if not line.startswith(key))
    with pytest.raises(ConfigFileError, match=key):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("line", "replacement"),
    [
        ("alpha = 0.05", "alpha = 0"),
        ("alpha = 0.05", "alpha = 1"),
        ("alpha = 0.05", 'alpha = "0.05"'),
        ("min_effect = 0.05", "min_effect = -0.1"),
        ("min_effect = 0.05", "min_effect = 1.0"),
        ("min_samples = 30", "min_samples = 0"),
        ("min_samples = 30", "min_samples = 1.5"),
        ("max_judge_error_rate = 0.2", "max_judge_error_rate = 1.5"),
        ("max_judge_error_rate = 0.2", "max_judge_error_rate = -0.1"),
        ("samples = 1", "samples = 0"),
        ('target_model_id_env = "TARGET_MODEL_ID"', "target_model_id_env = 3"),
        ('judge_model_id_env = "JUDGE_MODEL_ID"', 'judge_model_id_env = ""'),
    ],
)
def test_an_out_of_range_or_mistyped_value_is_a_typed_error(tmp_path, line, replacement):
    with pytest.raises(ConfigFileError):
        load_config(write_config(tmp_path, VALID.replace(line, replacement)))


def test_a_boolean_is_not_an_integer(tmp_path):
    """`true` is an int in Python; it is not a sample count."""
    with pytest.raises(ConfigFileError, match="samples"):
        load_config(write_config(tmp_path, VALID.replace("samples = 1", "samples = true")))


def test_an_unknown_key_is_refused(tmp_path):
    text = VALID.replace("alpha = 0.05", "alpha = 0.05\ntypo = 1")
    with pytest.raises(ConfigFileError, match="typo"):
        load_config(write_config(tmp_path, text))


def test_an_unknown_section_is_refused(tmp_path):
    with pytest.raises(ConfigFileError, match="reprot"):
        load_config(write_config(tmp_path, VALID + '\n[reprot]\nslack = "x"\n'))


def test_a_section_that_is_not_a_table_is_refused(tmp_path):
    text = 'run = "nope"\n' + VALID.replace("[run]\nsamples = 1\n", "")
    with pytest.raises(ConfigFileError, match="run"):
        load_config(write_config(tmp_path, text))
