"""Turn a `[target]` section of `regression.toml` into a `Target`.

The section is a boundary like any other: a human wrote it, so it is validated
key by key before anything is built, and every violation is a `TargetConfigError`
naming the kind or the key that is wrong. In particular an unknown key is an
error rather than an ignored line — a typo in `timeout_s` that silently left the
default in place would be a timeout nobody chose.

Only the builtin kind needs a model provider, so the provider is supplied as a
factory and called only for that kind. Pointing the detector at somebody else's
app must not require this repository's API key.
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...providers.base import Provider
from .base import Target, TargetConfigError
from .builtin import DEFAULT_TEMPERATURE, BuiltinSummarizerTarget
from .command import DEFAULT_TIMEOUT_S as COMMAND_TIMEOUT_S
from .command import CommandTarget
from .http import DEFAULT_TIMEOUT_S as HTTP_TIMEOUT_S
from .http import HttpTarget

REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "builtin": (),
    "command": ("argv",),
    "http": ("url",),
}

OPTIONAL_KEYS: dict[str, tuple[str, ...]] = {
    "builtin": ("prompt_path", "temperature"),
    "command": ("timeout_s", "cwd", "env_allowlist"),
    "http": ("timeout_s", "input_field", "output_field", "auth_header_env"),
}

KNOWN_KINDS = tuple(REQUIRED_KEYS)


def _checked_kind(config: Mapping[str, Any]) -> str:
    kind = config.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise TargetConfigError(
            f"[target] needs a 'kind': one of {', '.join(KNOWN_KINDS)}. Got {kind!r}."
        )
    kind = kind.strip()
    if kind not in REQUIRED_KEYS:
        raise TargetConfigError(
            f"[target] has an unknown kind {kind!r}; expected one of {', '.join(KNOWN_KINDS)}"
        )
    return kind


def _checked_keys(config: Mapping[str, Any], kind: str) -> None:
    allowed = {"kind", *REQUIRED_KEYS[kind], *OPTIONAL_KEYS[kind]}
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise TargetConfigError(
            f"[target] kind '{kind}' has no key(s): {', '.join(unknown)}. "
            f"It accepts: {', '.join(sorted(allowed))}"
        )
    missing = [key for key in REQUIRED_KEYS[kind] if key not in config]
    if missing:
        raise TargetConfigError(
            f"[target] kind '{kind}' is missing key(s): {', '.join(missing)}"
        )


def _temperature(config: Mapping[str, Any]) -> float:
    value = config.get("temperature", None)
    if value is None:
        return DEFAULT_TEMPERATURE
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TargetConfigError(f"[target] 'temperature' must be a number, got {value!r}")
    return float(value)


def _prompt_path(config: Mapping[str, Any]) -> Path | None:
    value = config.get("prompt_path", None)
    if value is None:
        return None
    if not isinstance(value, str | Path) or not str(value).strip():
        raise TargetConfigError(f"[target] 'prompt_path' must be a path, got {value!r}")
    return Path(str(value).strip())


def _builtin(config: Mapping[str, Any], provider_factory: Callable[[], Provider]) -> Target:
    prompt_path = _prompt_path(config)
    temperature = _temperature(config)
    provider = provider_factory()
    if prompt_path is None:
        return BuiltinSummarizerTarget(provider, temperature=temperature)
    return BuiltinSummarizerTarget(provider, prompt_path=prompt_path, temperature=temperature)


def load_target(
    config: Mapping[str, Any], *, provider_factory: Callable[[], Provider]
) -> Target:
    """Build the target a `[target]` section describes.

    Args:
        config: The section, already parsed from TOML.
        provider_factory: Called only for `kind = "builtin"`, to obtain the model
            provider the packaged summarizer runs on.

    Raises:
        TargetConfigError: if the section is not a table, names no kind, names an
            unknown one, omits a required key, carries a key that does not belong
            to its kind, or holds a value the adapter refuses.
    """
    if not isinstance(config, Mapping):
        raise TargetConfigError(
            f"[target] must be a table of settings, got {type(config).__name__}"
        )

    kind = _checked_kind(config)
    _checked_keys(config, kind)

    if kind == "builtin":
        return _builtin(config, provider_factory)
    if kind == "command":
        return CommandTarget(
            config["argv"],
            timeout_s=config.get("timeout_s", COMMAND_TIMEOUT_S),
            cwd=config.get("cwd"),
            env_allowlist=config.get("env_allowlist"),
        )
    return HttpTarget(
        config["url"],
        timeout_s=config.get("timeout_s", HTTP_TIMEOUT_S),
        input_field=config.get("input_field", "input"),
        output_field=config.get("output_field", "output"),
        auth_header_env=config.get("auth_header_env"),
    )
