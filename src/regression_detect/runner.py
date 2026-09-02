"""Stage 01: run every golden case through the target feature and record it.

This stage produces evidence, it does not grade. One model call per sample is
the only non-deterministic step; loading, hashing, timing, writing, and counting
are deterministic code.

A failing case is recorded and the run continues. Partial failure is visible in
the counts, in `outputs.jsonl`, in `review.md`, and in the exit code.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config_file import DEFAULT_TARGET_KIND, ConfigFileError, load_config
from .goldens import GoldenDatasetError, goldens_sha256, load_goldens
from .pacing import pace, validate_interval
from .providers.base import Provider, ProviderConfigError, ProviderError
from .providers.fake import FakeProvider
from .review import SampleResult, render_review
from .target.adapters.base import Target, TargetConfigError, TargetError, provenance_sha256
from .target.adapters.builtin import BuiltinSummarizerTarget
from .target.adapters.factory import load_target
from .target.adapters.fake import FakeTarget
from .target.config import target_model_id
from .target.summarizer import DEFAULT_PROMPT_PATH, SummarizerError

DEFAULT_GOLDENS_PATH = Path("goldens") / "cases.yaml"
DEFAULT_RUNS_DIR = Path("runs")
DRY_RUN_SUMMARY = (
    "The customer reports a problem with their order and is asking for help. "
    "This is a placeholder summary produced by a dry run; no model was called."
)

OUTPUTS_FILENAME = "outputs.jsonl"
MANIFEST_FILENAME = "manifest.json"
REVIEW_FILENAME = "review.md"


@dataclass(frozen=True)
class RunSummary:
    """What a completed stage-01 run produced."""

    out_dir: Path
    ok: int
    failed: int
    total: int

    @property
    def has_failures(self) -> bool:
        return self.failed > 0


def utc_stamp() -> str:
    """Timestamp for a manifest field."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_directory_name() -> str:
    """Filesystem-safe UTC timestamp used as the run directory name."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def display_path(path: Path) -> str:
    """Render a path relative to the working directory when possible.

    Absolute developer paths must not leak into committed or shared artifacts.
    """
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _run_one_sample(
    *,
    ticket: str,
    case_id: str,
    sample_index: int,
    target: Target,
    model_id: str,
    prompt_hash: str,
) -> SampleResult:
    """Run one case once. Typed failures become a recorded result, not a crash."""
    started = time.perf_counter()
    try:
        output = target.run(ticket)
    except (ProviderError, SummarizerError, TargetError) as exc:
        return SampleResult(
            case_id=case_id,
            sample_index=sample_index,
            output=None,
            model_id=model_id,
            prompt_sha256=prompt_hash,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return SampleResult(
        case_id=case_id,
        sample_index=sample_index,
        output=output,
        model_id=model_id,
        prompt_sha256=prompt_hash,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def run_goldens(
    *,
    goldens_path: Path,
    out_dir: Path,
    samples: int,
    provider: Provider | None = None,
    target: Target | None = None,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    temperature: float = 0.2,
    min_interval_ms: int = 0,
) -> RunSummary:
    """Run every golden case `samples` times and write the stage-01 outputs.

    The case goes through a `Target`, not through the summarizer directly, so a
    run can measure somebody else's feature. Passing `provider` and no `target`
    keeps the built-in summarizer, which is what every existing caller does.

    Raises:
        ValueError: if neither a provider nor a target was given, if `samples` is
            not a positive integer, or `min_interval_ms` is negative.
        GoldenDatasetError: if the dataset is missing or invalid.
        FileNotFoundError: if the prompt file is missing.
    """
    if not isinstance(samples, int) or samples < 1:
        raise ValueError(f"samples must be a positive integer, got {samples!r}")
    validate_interval(min_interval_ms)
    if target is None:
        if provider is None:
            raise ValueError("run_goldens needs either a provider or a target")
        target = BuiltinSummarizerTarget(
            provider, prompt_path=prompt_path, temperature=temperature
        )

    goldens_path = Path(goldens_path)
    cases = load_goldens(goldens_path)
    provenance = target.provenance()
    # A built-in run keeps the two identifiers baselines were always pinned to.
    # Any other target has neither, so its whole identity is hashed into the same
    # field: a target change is a hash change, and a hash change invalidates a
    # baseline instead of silently comparing two different features.
    prompt_hash = provenance.get("prompt_sha256") or provenance_sha256(provenance)
    model_id = provenance.get("model_id") or target.target_id
    started_at = utc_stamp()

    results: list[SampleResult] = []
    previous_start: float | None = None
    for case in cases:
        for sample_index in range(samples):
            previous_start = pace(previous_start, min_interval_ms)
            results.append(
                _run_one_sample(
                    ticket=case.input,
                    case_id=case.id,
                    sample_index=sample_index,
                    target=target,
                    model_id=model_id,
                    prompt_hash=prompt_hash,
                )
            )

    ok = sum(1 for result in results if result.ok)
    failed = len(results) - ok

    manifest: dict[str, Any] = {
        "run_id": Path(out_dir).name,
        "stage": "01_run",
        "started_at_utc": started_at,
        "finished_at_utc": utc_stamp(),
        "goldens_path": display_path(goldens_path),
        "goldens_sha256": goldens_sha256(goldens_path),
        "prompt_path": provenance.get("prompt_path", ""),
        "prompt_sha256": prompt_hash,
        "model_id": model_id,
        "provider_class": provenance.get("provider_class", type(target).__name__),
        "temperature": temperature,
        "samples": samples,
        "case_count": len(cases),
        "target_id": target.target_id,
        "target": provenance,
        "counts": {"ok": ok, "failed": failed, "total": len(results)},
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / OUTPUTS_FILENAME).write_text(
        "".join(json.dumps(result.to_json_row(), ensure_ascii=False) + "\n" for result in results),
        encoding="utf-8",
    )
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / REVIEW_FILENAME).write_text(
        render_review(cases, results, manifest), encoding="utf-8"
    )

    return RunSummary(out_dir=out_dir, ok=ok, failed=failed, total=len(results))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_goldens",
        description="Stage 01: run every golden case through the ticket summarizer.",
    )
    parser.add_argument(
        "--goldens", type=Path, default=DEFAULT_GOLDENS_PATH, help="Path to the golden dataset."
    )
    parser.add_argument(
        "--samples", type=int, default=1, help="How many times to run each case (default: 1)."
    )
    parser.add_argument(
        "--prompt", type=Path, default=DEFAULT_PROMPT_PATH, help="System prompt file to run."
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Directory that holds timestamped run directories (default: runs).",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2)."
    )
    parser.add_argument(
        "--min-interval-ms",
        type=int,
        default=0,
        help=(
            "Minimum milliseconds between consecutive target calls (default: 0). "
            "Set this to 60000/RPM when the provider quota is per minute."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Config file whose [target] section names the feature to run "
            "(default: the built-in summarizer, with no config file read)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Call nothing: a canned in-memory provider for the built-in target, and a "
            "canned in-memory target for any other kind the config names. No API key, "
            "no subprocess and no request, so no run reaches your app."
        ),
    )
    return parser


def build_provider(*, dry_run: bool) -> Provider:
    if dry_run:
        return FakeProvider(DRY_RUN_SUMMARY, model_id="dry-run-fake")
    from .providers.gemini import gemini_provider_from_env

    return gemini_provider_from_env(target_model_id())


def build_target(
    *,
    config_path: Path | None,
    dry_run: bool,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    temperature: float = 0.2,
) -> Target:
    """Pick the feature under test: the built-in summarizer unless a config names another.

    With no config file the behaviour is exactly what it was before targets
    existed. With one, the `[target]` section decides, and for the built-in kind
    the caller's `--prompt` and `--temperature` still win — varying the prompt is
    the whole reason stage 01 has those flags.

    A dry run is the exception, and it is not a small one. `--dry-run` promises
    no key, no network and no cost, and that promise cannot depend on what the
    config happens to name: a `command` target would spawn somebody's app and an
    `http` target would POST to somebody's endpoint. So for any non-builtin kind
    a dry run substitutes `FakeTarget` and never builds the configured adapter.
    The built-in kind keeps the behaviour it always had — the packaged summarizer
    on a canned provider — because that path already calls nothing.

    Raises:
        ConfigFileError: if the config file is missing or invalid.
        TargetConfigError: if its `[target]` section does not describe a target.
        ProviderConfigError: if a model provider is needed and cannot be built.
    """
    if config_path is None:
        return BuiltinSummarizerTarget(
            build_provider(dry_run=dry_run), prompt_path=prompt_path, temperature=temperature
        )

    section = load_config(config_path).target
    if section.get("kind") == DEFAULT_TARGET_KIND:
        section = {**section, "prompt_path": str(prompt_path), "temperature": temperature}
    elif dry_run:
        return FakeTarget(DRY_RUN_SUMMARY)
    return load_target(section, provider_factory=lambda: build_provider(dry_run=dry_run))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    try:
        target = build_target(
            config_path=args.config,
            dry_run=args.dry_run,
            prompt_path=args.prompt,
            temperature=args.temperature,
        )
        out_dir = Path(args.runs_dir) / run_directory_name()
        summary = run_goldens(
            goldens_path=args.goldens,
            out_dir=out_dir,
            samples=args.samples,
            target=target,
            prompt_path=args.prompt,
            temperature=args.temperature,
            min_interval_ms=args.min_interval_ms,
        )
    except (
        ConfigFileError,
        GoldenDatasetError,
        ProviderConfigError,
        TargetConfigError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"Run directory: {display_path(summary.out_dir)}")
    print(f"Cases: {summary.total} calls — {summary.ok} ok, {summary.failed} failed")
    print(f"Review by hand: {display_path(summary.out_dir / REVIEW_FILENAME)}")

    if summary.has_failures:
        print(
            f"{summary.failed} call(s) failed; see the 'error' field in "
            f"{OUTPUTS_FILENAME}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
