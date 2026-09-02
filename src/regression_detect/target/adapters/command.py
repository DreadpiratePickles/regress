"""Run somebody else's app as a subprocess: input on stdin, answer on stdout.

This is the adapter that makes the tool general. Most text-in/text-out features
can be exposed as a command that reads stdin and prints to stdout, and a command
needs no import of this package, no shared runtime, and no cooperation from the
app beyond an exit code.

Three rules are load-bearing and none of them is negotiable:

  - **argv is a list, never a string.** Nothing is handed to a shell, so a case
    input, a path, or a config value can never become a command. `shell=False`
    is passed explicitly to say so at the call site as well as in the type.
  - **The environment is an allowlist.** The child gets `PATH` and the variables
    the config names, and nothing else. A target that needs an API key says so;
    it does not inherit every secret the parent happens to hold.
  - **Diagnostics never carry the input.** A failing app's stderr is quoted back
    bounded and tail-first; the ticket that produced it is not, because a ticket
    is customer text and an error message travels further than a run directory.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .base import (
    TargetConfigError,
    TargetExecutionError,
    TargetResponseError,
    TargetTimeoutError,
    tail,
    validate_input_text,
    validate_timeout,
)

DEFAULT_TIMEOUT_S = 60.0
MAX_STDERR_CHARS = 500
"""How much of a failing app's stderr is quoted back, counted from the end."""

ALWAYS_ALLOWED_ENV = ("PATH",)
"""Variables the child always gets: without a PATH almost nothing is runnable."""


def _validate_argv(argv: object) -> tuple[str, ...]:
    if isinstance(argv, str) or not isinstance(argv, list | tuple):
        raise TargetConfigError(
            "'argv' must be a list of strings, not a shell string: "
            '["python", "app.py"], never "python app.py". Nothing is passed to a shell.'
        )
    if not argv:
        raise TargetConfigError("'argv' must name a program: the list is empty")
    for index, item in enumerate(argv):
        if not isinstance(item, str) or not item.strip():
            raise TargetConfigError(
                f"'argv' item {index} must be a non-empty string, got {item!r}"
            )
    return tuple(argv)


def _validate_cwd(cwd: object) -> Path | None:
    if cwd is None:
        return None
    if not isinstance(cwd, str | Path) or not str(cwd).strip():
        raise TargetConfigError(f"'cwd' must be a path, got {cwd!r}")
    path = Path(cwd)
    if not path.is_dir():
        raise TargetConfigError(f"'cwd' is not a directory: {path}")
    return path


def _validate_allowlist(env_allowlist: object) -> tuple[str, ...]:
    if env_allowlist is None:
        return ()
    if isinstance(env_allowlist, str) or not isinstance(env_allowlist, list | tuple):
        raise TargetConfigError(
            f"'env_allowlist' must be a list of variable names, got {env_allowlist!r}"
        )
    names = []
    for item in env_allowlist:
        if not isinstance(item, str) or not item.strip():
            raise TargetConfigError(
                f"'env_allowlist' must hold variable names, got {item!r}"
            )
        names.append(item.strip())
    return tuple(names)


class CommandTarget:
    """A feature under test that is invoked as a subprocess.

    Args:
        argv: The program and its arguments, as a list. Never a shell string.
        timeout_s: Seconds to wait for an answer before giving up.
        cwd: Working directory for the child, or `None` to inherit.
        env_allowlist: Variable names copied from this process's environment
            into the child's. `PATH` is always included; nothing else is.

    Raises:
        TargetConfigError: if any argument is unusable.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        cwd: Path | None = None,
        env_allowlist: list[str] | None = None,
    ) -> None:
        self._argv = _validate_argv(argv)
        self._timeout_s = validate_timeout(timeout_s)
        self._cwd = _validate_cwd(cwd)
        self._env_allowlist = _validate_allowlist(env_allowlist)
        self._argv_sha256 = hashlib.sha256(
            "\0".join(self._argv).encode("utf-8")
        ).hexdigest()
        self.target_id = f"command:{self._argv_sha256[:16]}"

    def _child_env(self) -> dict[str, str]:
        """The child's whole environment: the allowlist, and nothing besides."""
        wanted = (*ALWAYS_ALLOWED_ENV, *self._env_allowlist)
        return {name: os.environ[name] for name in wanted if name in os.environ}

    def run(self, input_text: str) -> str:
        """Send `input_text` on stdin and return the stripped stdout.

        Raises:
            TargetExecutionError: the program could not be started, or exited
                non-zero. The message carries the exit code and the tail of
                stderr, never the input.
            TargetTimeoutError: the program did not answer in time.
            TargetResponseError: the program exited 0 but printed nothing.
        """
        text = validate_input_text(input_text, target_id=self.target_id)

        try:
            completed = subprocess.run(  # noqa: S603 — argv list, shell=False, see module docstring
                list(self._argv),
                input=text,
                text=True,
                capture_output=True,
                timeout=self._timeout_s,
                shell=False,
                cwd=self._cwd,
                env=self._child_env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TargetTimeoutError(
                f"{self.target_id} ({self._argv[0]}) gave no answer within "
                f"{self._timeout_s} seconds"
            ) from exc
        except OSError as exc:
            raise TargetExecutionError(
                f"{self.target_id} could not be started ({self._argv[0]}): {exc.strerror}"
            ) from exc

        if completed.returncode != 0:
            raise TargetExecutionError(
                f"{self.target_id} exited {completed.returncode}. "
                f"stderr: {tail(completed.stderr, MAX_STDERR_CHARS)}"
            )

        output = (completed.stdout or "").strip()
        if not output:
            raise TargetResponseError(
                f"{self.target_id} exited 0 but wrote nothing to stdout; "
                f"stderr: {tail(completed.stderr, MAX_STDERR_CHARS)}"
            )
        return output

    def provenance(self) -> dict[str, str]:
        """The argv, its hash, and the sandbox the child was given."""
        return {
            "kind": "command",
            "target_id": self.target_id,
            "argv": json.dumps(list(self._argv), ensure_ascii=False),
            "argv_sha256": self._argv_sha256,
            "timeout_s": str(self._timeout_s),
            "cwd": str(self._cwd) if self._cwd is not None else "",
            "env_allowlist": ",".join(self._env_allowlist),
        }
