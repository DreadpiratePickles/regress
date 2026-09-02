"""Read `regression.toml`, the file that holds the thresholds a verdict rests on.

The numbers that decide whether a build fails are policy, not implementation
detail, so they live in a reviewable file at the repository root instead of as
literals inside `compare.py`. That makes the file a boundary like any other:
it is parsed with the standard library, validated key by key, and every
violation is a typed error naming the key that is wrong.

Model identifiers are deliberately not here. They live in `target/config.py`
and `judge/config.py`; this file only records the names of the environment
variables that override them.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("regression.toml")


class ConfigFileError(Exception):
    """`regression.toml` is missing, unparseable, or holds an unusable value."""


@dataclass(frozen=True)
class CompareSettings:
    """The thresholds stage 03 decides with."""

    alpha: float
    min_effect: float
    min_samples: int
    max_judge_error_rate: float


@dataclass(frozen=True)
class RunSettings:
    """Defaults for the pipeline's call into stage 01."""

    samples: int


@dataclass(frozen=True)
class ModelEnvSettings:
    """Names of the environment variables that override the two model ids."""

    target_model_id_env: str
    judge_model_id_env: str


@dataclass(frozen=True)
class RegressionConfig:
    """The whole of `regression.toml`, validated."""

    compare: CompareSettings
    run: RunSettings
    models: ModelEnvSettings


SECTIONS = {
    "compare": ("alpha", "min_effect", "min_samples", "max_judge_error_rate"),
    "run": ("samples",),
    "models": ("target_model_id_env", "judge_model_id_env"),
}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise ConfigFileError(
            f"Configuration file not found: {path}. The repository root must hold a "
            "regression.toml; copy the committed one rather than inventing thresholds."
        ) from exc
    except OSError as exc:
        raise ConfigFileError(f"Configuration file could not be read: {path}") from exc

    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigFileError(f"Configuration file is not valid TOML: {path} ({exc})") from exc


def _section(document: dict[str, Any], name: str, *, path: Path) -> dict[str, Any]:
    if name not in document:
        raise ConfigFileError(f"{path}: missing required section [{name}]")
    value = document[name]
    if not isinstance(value, dict):
        raise ConfigFileError(
            f"{path}: [{name}] must be a table, got {type(value).__name__}"
        )
    unknown = sorted(set(value) - set(SECTIONS[name]))
    if unknown:
        raise ConfigFileError(f"{path}: [{name}] has unknown key(s): {', '.join(unknown)}")
    missing = [key for key in SECTIONS[name] if key not in value]
    if missing:
        raise ConfigFileError(f"{path}: [{name}] is missing key(s): {', '.join(missing)}")
    return value


def _fraction(
    section: dict[str, Any],
    key: str,
    *,
    path: Path,
    low: float,
    high: float,
    low_inclusive: bool = True,
    high_inclusive: bool = True,
) -> float:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigFileError(
            f"{path}: '{key}' must be a number, got {type(value).__name__}"
        )
    value = float(value)
    below = value < low if low_inclusive else value <= low
    above = value > high if high_inclusive else value >= high
    if below or above:
        left, right = ("[", "]") if low_inclusive else ("(", ")")
        raise ConfigFileError(
            f"{path}: '{key}' must lie in {left}{low}, {high}{right if high_inclusive else ')'}"
            f", got {value}"
        )
    return value


def _positive_int(section: dict[str, Any], key: str, *, path: Path) -> int:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigFileError(
            f"{path}: '{key}' must be an integer, got {type(value).__name__}"
        )
    if value < 1:
        raise ConfigFileError(f"{path}: '{key}' must be at least 1, got {value}")
    return value


def _non_empty_str(section: dict[str, Any], key: str, *, path: Path) -> str:
    value = section[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigFileError(f"{path}: '{key}' must be a non-empty string")
    return value.strip()


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> RegressionConfig:
    """Read and validate `regression.toml`.

    Raises:
        ConfigFileError: if the file is missing, is not TOML, omits a section or
            key, carries an unknown one, or holds a value outside its range.
    """
    path = Path(path)
    document = _read_toml(path)

    unknown = sorted(set(document) - set(SECTIONS))
    if unknown:
        raise ConfigFileError(f"{path}: unknown section(s): {', '.join(unknown)}")

    compare = _section(document, "compare", path=path)
    run = _section(document, "run", path=path)
    models = _section(document, "models", path=path)

    return RegressionConfig(
        compare=CompareSettings(
            alpha=_fraction(
                compare, "alpha", path=path, low=0.0, high=1.0, low_inclusive=False,
                high_inclusive=False,
            ),
            min_effect=_fraction(
                compare, "min_effect", path=path, low=0.0, high=1.0, high_inclusive=False
            ),
            min_samples=_positive_int(compare, "min_samples", path=path),
            max_judge_error_rate=_fraction(
                compare, "max_judge_error_rate", path=path, low=0.0, high=1.0
            ),
        ),
        run=RunSettings(samples=_positive_int(run, "samples", path=path)),
        models=ModelEnvSettings(
            target_model_id_env=_non_empty_str(models, "target_model_id_env", path=path),
            judge_model_id_env=_non_empty_str(models, "judge_model_id_env", path=path),
        ),
    )
