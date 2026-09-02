"""Stage 03, part one: turn judged runs into a baseline.

A baseline is what "before" looked like. It is not one run's pass rate: it is a
per-criterion count of how many times that criterion was judged and how many
times it passed, pooled over as many runs as you care to record. That pooling is
the whole point — a single run's 100% and a single run's 93% are usually the same
model on two different afternoons, and only counts across runs can tell you
which drops are noise.

Everything here is deterministic: reading, checking, counting, writing. No model
is called and no judgement is made.

Runs are only pooled when they are actually comparable: same goldens, same
target prompt, same target model, same judge prompt, same judge model. A
baseline that mixed two prompts would measure the mixture, not the feature.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .baseline_inputs import (
    BaselineInputError,
    RunProvenance,
    VerdictRecord,
    read_provenance,
    read_verdicts,
)
from .runner import display_path

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "Baseline",
    "BaselineInputError",
    "CriterionStat",
    "build_baseline",
    "build_parser",
    "main",
    "render_table",
]
"""The stage's public surface. `BaselineInputError` is re-exported from
`baseline_inputs` so a caller only has to know about the stage, not its readers."""

MATCHED_FIELDS = (
    ("goldens_sha256", "goldens_sha256"),
    ("prompt_sha256", "prompt_sha256"),
    ("target_model_id", "model_id"),
    ("judge_prompt_sha256", "judge_prompt_sha256"),
    ("judge_model_id", "judge_model_id"),
)
"""Attribute on `RunProvenance`, and the manifest key it came from. The manifest
key is what the error message names, because that is what an operator can go and
look at."""


@dataclass(frozen=True)
class CriterionStat:
    """How one criterion fared across every run in the baseline."""

    case_id: str
    criterion_index: int
    criterion: str
    n: int
    """Verdicts with a real boolean. Judge errors are not counted here."""
    passes: int
    judge_errors: int

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.case_id, self.criterion_index, self.criterion)

    @property
    def pass_rate(self) -> float | None:
        """`None`, never zero, when nothing was judged: no evidence is not failure."""
        return (self.passes / self.n) if self.n else None

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "criterion_index": self.criterion_index,
            "criterion": self.criterion,
            "n": self.n,
            "passes": self.passes,
            "judge_errors": self.judge_errors,
        }

    @classmethod
    def from_json(cls, payload: Any) -> "CriterionStat":
        if not isinstance(payload, dict):
            raise BaselineInputError("A baseline criterion must be a JSON object")
        try:
            return cls(
                case_id=str(payload["case_id"]),
                criterion_index=int(payload["criterion_index"]),
                criterion=str(payload["criterion"]),
                n=int(payload["n"]),
                passes=int(payload["passes"]),
                judge_errors=int(payload["judge_errors"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BaselineInputError(f"A baseline criterion is malformed: {exc}") from exc


@dataclass(frozen=True)
class Baseline:
    """Pooled per-criterion counts, plus the provenance that makes them comparable."""

    schema_version: int
    created_at_utc: str
    run_ids: tuple[str, ...]
    goldens_sha256: str
    prompt_sha256: str
    target_model_id: str
    judge_prompt_sha256: str
    judge_model_id: str
    criteria: tuple[CriterionStat, ...]
    total_n: int
    total_passes: int
    judge_errors: int

    @property
    def pass_rate(self) -> float | None:
        return (self.total_passes / self.total_n) if self.total_n else None

    def by_key(self) -> dict[tuple[str, int, str], CriterionStat]:
        return {item.key: item for item in self.criteria}

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at_utc": self.created_at_utc,
            "run_ids": list(self.run_ids),
            "goldens_sha256": self.goldens_sha256,
            "prompt_sha256": self.prompt_sha256,
            "target_model_id": self.target_model_id,
            "judge_prompt_sha256": self.judge_prompt_sha256,
            "judge_model_id": self.judge_model_id,
            "totals": {
                "n": self.total_n,
                "passes": self.total_passes,
                "judge_errors": self.judge_errors,
            },
            "criteria": [item.to_json() for item in self.criteria],
        }

    @classmethod
    def from_json(cls, payload: Any) -> "Baseline":
        """Rebuild a baseline from its JSON form.

        Raises:
            BaselineInputError: if the payload is not an object, declares a
                schema version this code does not understand, or omits a field.
        """
        if not isinstance(payload, dict):
            raise BaselineInputError("A baseline file must hold a JSON object")
        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise BaselineInputError(
                f"Baseline schema_version {version!r} is not supported; this code reads "
                f"version {SCHEMA_VERSION}."
            )
        totals = payload.get("totals")
        if not isinstance(totals, dict):
            raise BaselineInputError("A baseline file must carry a 'totals' object")
        criteria = payload.get("criteria")
        if not isinstance(criteria, list):
            raise BaselineInputError("A baseline file must carry a 'criteria' list")

        try:
            return cls(
                schema_version=SCHEMA_VERSION,
                created_at_utc=str(payload["created_at_utc"]),
                run_ids=tuple(str(item) for item in payload["run_ids"]),
                goldens_sha256=str(payload["goldens_sha256"]),
                prompt_sha256=str(payload["prompt_sha256"]),
                target_model_id=str(payload["target_model_id"]),
                judge_prompt_sha256=str(payload["judge_prompt_sha256"]),
                judge_model_id=str(payload["judge_model_id"]),
                criteria=tuple(CriterionStat.from_json(item) for item in criteria),
                total_n=int(totals["n"]),
                total_passes=int(totals["passes"]),
                judge_errors=int(totals["judge_errors"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BaselineInputError(f"A baseline file is malformed: {exc}") from exc


def utc_stamp() -> str:
    """The moment the baseline was built, recorded in the file."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agreed_provenance(provenances: list[RunProvenance]) -> RunProvenance:
    """Confirm every run agrees on what it measured, or refuse to pool them."""
    first = provenances[0]
    for other in provenances[1:]:
        for attribute, manifest_key in MATCHED_FIELDS:
            mine, theirs = getattr(first, attribute), getattr(other, attribute)
            if mine != theirs:
                raise BaselineInputError(
                    f"Runs disagree on '{manifest_key}': {first.run_id} has {mine!r} but "
                    f"{other.run_id} has {theirs!r}. A baseline may only pool runs that "
                    "measured the same thing."
                )
    return first


def _accumulate(records: list[VerdictRecord]) -> dict[tuple[str, int, str], list[int]]:
    """Count n, passes and judge errors per criterion key."""
    counts: dict[tuple[str, int, str], list[int]] = {}
    for record in records:
        tally = counts.setdefault(record.key, [0, 0, 0])
        if record.passed is None:
            tally[2] += 1
            continue
        tally[0] += 1
        tally[1] += int(record.passed)
    return counts


def build_baseline(run_dirs: list[Path]) -> Baseline:
    """Pool one or more judged run directories into a baseline.

    Raises:
        BaselineInputError: if no run is given, if a run is missing or malformed,
            or if the runs did not all measure the same thing.
    """
    directories = [Path(item) for item in run_dirs]
    if not directories:
        raise BaselineInputError("A baseline needs at least one judged run directory.")

    provenances = [read_provenance(directory) for directory in directories]
    agreed = _agreed_provenance(provenances)

    records: list[VerdictRecord] = []
    for directory in directories:
        records.extend(read_verdicts(directory))

    counts = _accumulate(records)
    criteria = tuple(
        CriterionStat(
            case_id=key[0],
            criterion_index=key[1],
            criterion=key[2],
            n=tally[0],
            passes=tally[1],
            judge_errors=tally[2],
        )
        for key, tally in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1]))
    )

    return Baseline(
        schema_version=SCHEMA_VERSION,
        created_at_utc=utc_stamp(),
        run_ids=tuple(item.run_id for item in provenances),
        goldens_sha256=agreed.goldens_sha256,
        prompt_sha256=agreed.prompt_sha256,
        target_model_id=agreed.target_model_id,
        judge_prompt_sha256=agreed.judge_prompt_sha256,
        judge_model_id=agreed.judge_model_id,
        criteria=criteria,
        total_n=sum(item.n for item in criteria),
        total_passes=sum(item.passes for item in criteria),
        judge_errors=sum(item.judge_errors for item in criteria),
    )


def read_baseline(path: Path) -> Baseline:
    """Read a baseline file from disk.

    Raises:
        BaselineInputError: if it is missing, unreadable, not JSON, or malformed.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BaselineInputError(f"Baseline not found: {display_path(path)}") from exc
    except OSError as exc:
        raise BaselineInputError(f"Baseline could not be read: {display_path(path)}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BaselineInputError(
            f"Baseline is not valid JSON: {display_path(path)} ({exc.msg})"
        ) from exc
    return Baseline.from_json(payload)


def write_baseline(baseline: Baseline, path: Path) -> Path:
    """Write a baseline as pretty JSON, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def _percent(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.1f}%"


def render_table(baseline: Baseline) -> str:
    """Render a baseline as the table printed by `baseline.py show`."""
    lines = [
        f"Baseline — {len(baseline.criteria)} criteria from {len(baseline.run_ids)} run(s)",
        f"  Built            {baseline.created_at_utc}",
        f"  Runs             {', '.join(baseline.run_ids)}",
        f"  Target model     {baseline.target_model_id}",
        f"  Judge model      {baseline.judge_model_id}",
        f"  Goldens sha256   {baseline.goldens_sha256[:12]}…",
        f"  Prompt sha256    {baseline.prompt_sha256[:12]}…",
        f"  Judge prompt     {baseline.judge_prompt_sha256[:12]}…",
        f"  Pooled pass rate {_percent(baseline.pass_rate)} "
        f"({baseline.total_passes}/{baseline.total_n})",
        f"  Judge errors     {baseline.judge_errors}",
        "",
        f"  {'case':<28} {'#':>2}  {'passes':>7}  {'rate':>6}  criterion",
    ]
    for item in baseline.criteria:
        lines.append(
            f"  {item.case_id:<28} {item.criterion_index + 1:>2}  "
            f"{f'{item.passes}/{item.n}':>7}  {_percent(item.pass_rate):>6}  {item.criterion}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="baseline",
        description="Build and inspect the baseline that stage 03 compares against.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Pool judged runs into a baseline file.")
    build.add_argument(
        "--runs",
        type=Path,
        nargs="+",
        required=True,
        help="One or more judged run directories to pool.",
    )
    build.add_argument(
        "--out", type=Path, required=True, help="Where to write the baseline JSON."
    )

    show = subparsers.add_parser("show", help="Print a baseline as a table.")
    show.add_argument("--baseline", type=Path, required=True, help="The baseline file to read.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    try:
        if args.command == "build":
            baseline = build_baseline(list(args.runs))
            destination = write_baseline(baseline, args.out)
        else:
            baseline = read_baseline(args.baseline)
            destination = None
    except BaselineInputError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(render_table(baseline))
    if destination is not None:
        print(f"\nWritten: {display_path(destination)}")
        print(
            "Commit it: the baseline is the reference CI compares against, and a "
            "baseline nobody reviewed is a threshold nobody agreed to."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
