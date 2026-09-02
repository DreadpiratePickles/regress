# Stage: 02_judge

## Objective

Grade every output a stage-01 run recorded against the criteria of its golden
case, one criterion per model call, and emit validated structured scores plus a
document a human can check the judge against.

## Inputs

| Path or source | Layer | Authority | Required | Relevant section |
|---|---:|---|---:|---|
| `runs/<UTC timestamp>/outputs.jsonl` | 4 | Authoritative | Yes | Every row: `case_id`, `sample_index`, `output` |
| `runs/<UTC timestamp>/manifest.json` | 4 | Authoritative | Yes | `goldens_sha256` — the dataset this run was produced from |
| `goldens/cases.yaml` | 3 | Authoritative | Yes | `criteria` per case, in order; `input` is re-sent as context |
| `src/regression_detect/judge/prompts/judge_v1.md` | 3 | Authoritative | Yes | Whole file: sent as the judge system prompt |
| `src/regression_detect/judge/config.py` | 3 | Authoritative | Yes | `judge_model_id()` — the model called |
| `JUDGE_MODEL_ID` (environment) | 3 | Override | No | Overrides the default judge model id |
| `GEMINI_API_KEY` (`.env`, environment) | 3 | Authoritative | Yes, unless `--dry-run` | The provider credential; never logged |
| `--judge-samples` (CLI) | 4 | Operator input | No | Judge calls per criterion; default 1 |
| `--min-interval-ms` (CLI) | 4 | Operator input | No | Minimum gap between judge calls; default 0. Set to `60000 / RPM` under a per-minute provider quota |

The run's `review.md` is **not** an input to this stage. The judge must not see
the human's ticks; calibration compares the two afterwards.

## Process

Steps 1–6 and 8–12 are deterministic code. Step 7 is the only model call.

1. Parse and validate CLI arguments; reject a non-positive `--judge-samples`
   or a negative `--min-interval-ms`.
2. Build the provider: `FakeProvider` under `--dry-run`, otherwise a Gemini
   provider from `GEMINI_API_KEY`. A missing key fails here and never reaches
   step 7.
3. Read and validate the run's `manifest.json` and every row of
   `outputs.jsonl` — a row must carry a non-empty string `case_id`, an integer
   `sample_index`, and an `output` that is a string or null. A malformed row
   aborts before any spend.
4. Load and validate the golden dataset, hash it, and compare that hash against
   the run manifest's `goldens_sha256`. A mismatch aborts: the criteria would
   not be the ones the outputs were produced for.
5. Skip and count every row whose `output` is null. Stage 01 already recorded
   why it failed; there is nothing to grade.
6. For each remaining row, for each criterion of its case, for each judge
   sample: wait until `--min-interval-ms` has elapsed since the previous call
   started, then build the user message wrapping the ticket, the summary and the
   one criterion in `<ticket>`, `<summary>` and `<criterion>` delimiters. None
   of the three is ever formatted into the system prompt.
7. **Model call.** One criterion, one verdict. Temperature 0.0.
8. Validate the reply: exactly the keys `reason` and `passed`, a real JSON
   boolean, a non-empty reason. Surrounding whitespace and a single ```json
   fence are tolerated; anything else is a `JudgeParseError`. Model output is
   untrusted input.
9. Record the verdict, or the error that replaced it. A failed call is recorded
   with `passed: null` and never as `passed: false`.
10. Count per case: passed, failed, errored. Compute `pass_rate` as
    `passed / (passed + failed)` — errored criteria are excluded, not failed.
11. Write `verdicts.jsonl`, `judge_manifest.json`, `scores.json`, `judged.md`
    into the same run directory. Stage 01's files are never modified.
12. Print the counts and exit non-zero if any judge call failed.

Calibration (`scripts/calibrate.py`) is a separate, entirely deterministic
step: it parses the human ticks out of `review.md`, compares them against the
verdicts, and counts agreement, false passes and false fails. It calls no model.

## Outputs

| Path | Schema or format | Consumer |
|---|---|---|
| `runs/<ts>/verdicts.jsonl` | One JSON object per line: `case_id`, `sample_index`, `criterion_index`, `criterion`, `judge_sample_index`, `passed` (bool or null), `reason` (string or null), `judge_model_id`, `judge_prompt_sha256`, `latency_ms`, `error_type` (string or null), `error` (string or null) | Stage 03 (compare), calibration |
| `runs/<ts>/judge_manifest.json` | JSON: `run_id`, `stage`, `started_at_utc`, `finished_at_utc`, `goldens_path`, `goldens_sha256`, `judge_prompt_path`, `judge_prompt_sha256`, `judge_model_id`, `provider_class`, `temperature`, `judge_samples`, `counts.{verdicts_ok,judge_errors,skipped_outputs}` | Stage 03, and any human auditing provenance |
| `runs/<ts>/scores.json` | JSON: `run_id`, `cases.<case_id>.{criteria_total,passed,failed,errored,pass_rate}`, `overall.{…}`. `pass_rate` is null when nothing was judged | Stage 03 (compare) |
| `runs/<ts>/judged.md` | Markdown: per case the pass rate, the output, then every criterion marked ✅ / ❌ / ⚠️ with the judge's reason | A human checking the judge |
| `runs/<ts>/calibration.json` | JSON: `run_id`, `graded_cases`, `compared`, `agreements`, `agreement_rate`, `false_pass`, `false_fail`, `judge_errors`, `not_judged`, `mismatches[]` | A human deciding whether to trust the judge |
| Process exit code | `0` no judge errors · `1` at least one judge call failed · `2` bad configuration or mismatched goldens | CI |

## Verify

- `uv run pytest -q` — 364 tests, all passing, none touching the network.
- `uv run ruff check .` — clean at line-length 100.
- `uv run pytest --cov=regression_detect --cov-report=term-missing -q` — 96%.
- `uv run python scripts/run_goldens.py --dry-run` then
  `uv run python scripts/judge_run.py --run runs/<ts> --dry-run` — exits 0 and
  writes all four files, with `verdicts.jsonl` holding one row per criterion
  (67 for the current dataset) and `scores.json` agreeing with those rows.
- After a real run: `judge_manifest.json` counts equal the line count of
  `verdicts.jsonl`; `goldens_sha256` equals the stage-01 manifest's;
  `judge_prompt_sha256` matches `sha256sum` of `judge_v1.md`.
- `uv run python scripts/calibrate.py --run runs/<ts> --graded-cases <ids>`
  after a human has ticked `review.md`.
- Evidence that the stage worked is a human reading `judged.md` beside
  `review.md` and a calibration table, not a green test run. This stage grades
  stage 01; nothing here grades itself.

## Approval

A human owns the verdict on the judge. Before any judge score is trusted:

1. They grade `review.md` by hand, without reading `judged.md` first — reading
   the judge's answers before writing their own contaminates the labels.
2. They run `calibrate.py` naming only the cases they actually graded, and read
   the mismatch list. `false_pass` is the count that matters: a regression the
   judge waves through is a regression the tool will not report.

Blocked without their approval: promoting a run's `scores.json` to a baseline,
changing `goldens/cases.yaml` in response to a judge verdict, and editing
`judge_v1.md` to make a disputed criterion pass. The stage performs no external
write beyond the model calls and the four files it adds to the run directory.

The judge currently runs on the same model as the target because only one
provider key exists. That is a known **self-preference bias** — see
`judge/config.py`. Calibration is what keeps it honest until a second key
allows a judge from a different model family.

## Failure Behavior

| Failure | Behavior |
|---|---|
| `GEMINI_API_KEY` missing or rejected | `ProviderConfigError` before any criterion is judged; exit 2; message names the variable, never the key |
| Run directory, `manifest.json` or `outputs.jsonl` missing or malformed | `JudgeRunError` naming the file and line; exit 2; nothing is written into the run directory |
| Goldens hash differs from the run manifest's | `GoldensMismatchError`; exit 2. Never judged against a dataset the outputs did not come from |
| Golden dataset invalid | `GoldenDatasetError` naming the offending case; exit 2 |
| Judge prompt file missing | `FileNotFoundError`; exit 2. Never falls back to an unguided judge |
| Stage-01 output is null for a row | Skipped, counted as `skipped_outputs`, shown as "not judged" in `judged.md`. Not a failed criterion |
| Rate limit, timeout, or 5xx on one judge call | Retried inside the provider (3 attempts, backoff with jitter); still failing, the verdict is recorded with `error_type` and the run continues |
| Most judge calls fail with 429 | The stage is outrunning a per-minute quota; bounded retries cannot wait out a window that long. Re-run with `--min-interval-ms 60000/RPM`. Observed on the free tier: unpaced, 61 of 67 criteria were lost this way |
| Judge reply will not parse | `JudgeParseError` recorded with `passed: null`. Never degraded to `passed: false` |
| Any judge call failed | All four files are still written; exit 1 so CI does not read a partial judgement as a pass |
| Calibration compares zero criteria | `CalibrationError`; exit 2 with the counts that were excluded. Never reports a 0% or 100% agreement rate computed from nothing |

No cleanup or rollback is needed: the stage only appends files to a run
directory and `runs/` is gitignored. Escalation path: a run where every judge
call failed is a configuration or provider problem, not a regression — check the
key, the judge model id, and the provider status before reading the scores. A
run where the judge parsed but disagrees with the human everywhere is a judge
problem — fix `judge_v1.md` and re-judge; do not touch the goldens.
