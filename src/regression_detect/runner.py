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

from .goldens import GoldenDatasetError, goldens_sha256, load_goldens
from .providers.base import Provider, ProviderConfigError, ProviderError
from .providers.fake import FakeProvider
from .review import SampleResult, render_review
from .target.config import target_model_id
from .target.summarizer import DEFAULT_PROMPT_PATH, SummarizerError, prompt_sha256, summarize

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
    provider: Provider,
    prompt_path: Path,
    prompt_hash: str,
    temperature: float,
) -> SampleResult:
    """Run one case once. Typed failures become a recorded result, not a crash."""
    started = time.perf_counter()
    try:
        output = summarize(ticket, provider, prompt_path=prompt_path, temperature=temperature)
    except (ProviderError, SummarizerError) as exc:
        return SampleResult(
            case_id=case_id,
            sample_index=sample_index,
            output=None,
            model_id=provider.model_id,
            prompt_sha256=prompt_hash,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return SampleResult(
        case_id=case_id,
        sample_index=sample_index,
        output=output,
        model_id=provider.model_id,
        prompt_sha256=prompt_hash,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def run_goldens(
    *,
    goldens_path: Path,
    out_dir: Path,
    samples: int,
    provider: Provider,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    temperature: float = 0.2,
) -> RunSummary:
    """Run every golden case `samples` times and write the stage-01 outputs.

    Raises:
        ValueError: if `samples` is not a positive integer.
        GoldenDatasetError: if the dataset is missing or invalid.
        FileNotFoundError: if the prompt file is missing.
    """
    if not isinstance(samples, int) or samples < 1:
        raise ValueError(f"samples must be a positive integer, got {samples!r}")

    goldens_path = Path(goldens_path)
    prompt_path = Path(prompt_path)
    cases = load_goldens(goldens_path)
    prompt_hash = prompt_sha256(prompt_path)
    started_at = utc_stamp()

    results: list[SampleResult] = []
    for case in cases:
        for sample_index in range(samples):
            results.append(
                _run_one_sample(
                    ticket=case.input,
                    case_id=case.id,
                    sample_index=sample_index,
                    provider=provider,
                    prompt_path=prompt_path,
                    prompt_hash=prompt_hash,
                    temperature=temperature,
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
        "prompt_path": display_path(prompt_path),
        "prompt_sha256": prompt_hash,
        "model_id": provider.model_id,
        "provider_class": type(provider).__name__,
        "temperature": temperature,
        "samples": samples,
        "case_count": len(cases),
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
        "--dry-run",
        action="store_true",
        help="Use a canned in-memory provider instead of calling a model. No API key needed.",
    )
    return parser


def _build_provider(*, dry_run: bool) -> Provider:
    if dry_run:
        return FakeProvider(DRY_RUN_SUMMARY, model_id="dry-run-fake")
    from .providers.gemini import gemini_provider_from_env

    return gemini_provider_from_env(target_model_id())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    try:
        provider = _build_provider(dry_run=args.dry_run)
        out_dir = Path(args.runs_dir) / run_directory_name()
        summary = run_goldens(
            goldens_path=args.goldens,
            out_dir=out_dir,
            samples=args.samples,
            provider=provider,
            prompt_path=args.prompt,
            temperature=args.temperature,
        )
    except (GoldenDatasetError, ProviderConfigError, FileNotFoundError, ValueError) as exc:
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
