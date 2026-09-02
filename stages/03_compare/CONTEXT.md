# Stage: 03_compare

## Objective

Decide whether a judged run's scores are worse than the committed baseline's by
more than run-to-run noise, and say so in a verdict, an exit code and a sentence
a human can check.

## Inputs

| Path or source | Layer | Authority | Required | Relevant section |
|---|---:|---|---:|---|
| `baselines/<target>/baseline.json` | 3 | Authoritative | Yes | Whole file: per-criterion counts and the provenance they were measured under |
| `runs/<ts>/verdicts.jsonl` | 4 | Authoritative | Yes | Every row: `case_id`, `criterion_index`, `criterion`, `passed` |
| `runs/<ts>/manifest.json` | 4 | Authoritative | Yes | `goldens_sha256`, `prompt_sha256`, `model_id` |
| `runs/<ts>/judge_manifest.json` | 4 | Authoritative | Yes | `judge_prompt_sha256`, `judge_model_id` |
| `regression.toml` | 3 | Authoritative | Yes | `[compare]`: `alpha`, `min_effect`, `min_samples`, `max_judge_error_rate`; `[run] samples` for `detect.py` |
| `baselines/README.md` | 3 | Authoritative | No | The rules a baseline must satisfy — read before re-baselining |
| `docs/statistics.md` | 3 | Explanatory | No | Why each test is the one used |
| `--samples`, `--min-interval-ms`, `--prompt` (CLI) | 4 | Operator input | No | Passed straight through to stages 01 and 02 |

`runs/<ts>/scores.json` is **not** an input. It holds per-case pass rates, and a
ratio cannot be tested; stage 03 reads the verdict rows and keeps counts.

The candidate's `review.md` and `judged.md` are not inputs either. This stage
does statistics on recorded verdicts; it never re-reads an output or a summary.

## Process

**Every step is deterministic.** No model is called anywhere in this stage. The
same two baselines always produce the same verdict, the same p-value and the same
sentence — that is the property that makes the verdict reviewable at all.

1. Read and validate `regression.toml`; every key is range-checked and any
   unknown key or section is an error.
2. Read the baseline file. `detect.py` does this **before** stage 01 runs, so a
   missing or malformed baseline costs nothing rather than 67 paid calls.
3. Read the candidate run: both manifests and every row of `verdicts.jsonl`,
   validated field by field.
4. Aggregate the candidate into a `Baseline` of its own — the same shape as the
   reference, so the two are comparable and so a candidate can be promoted with
   one `baseline build`. Verdicts with `passed: null` are excluded from `n` and
   counted as `judge_errors`; they are never counted as failures.
5. Match criteria on `(case_id, criterion_index, criterion)`. The text is part of
   the identity: an edited criterion is a different question. Anything present on
   one side only is collected as `unmatched` and excluded from every number.
6. Pool the matched criteria on each side into passes and n.
7. Compute the one-sided Fisher exact p-value from the 2×2 table with
   `math.comb`, and a Wilson 95% interval for each rate.
8. Flag each matched criterion as a **hard regression** when the baseline judged
   it at least twice and passed every time, and the candidate judged it at least
   twice and failed every time.
9. Decide the verdict, in this order:
   - `REGRESSION` if any hard regression, or if the drop is at least
     `min_effect` **and** p is below `alpha`;
   - else `INCONCLUSIVE` if nothing matched, or the candidate's matched n is
     below `min_samples`, or its judge errors exceed `max_judge_error_rate` of
     its rows;
   - else `NO_REGRESSION`.

   A proven regression outranks a thin sample on purpose: the finding is that
   the evidence exists. `INCONCLUSIVE` is for when it does not.
10. Render the explanation, write `comparison.json` into the candidate run
    directory, print the report, and exit with the verdict's code.

## Outputs

| Path | Schema or format | Consumer |
|---|---|---|
| `baselines/<target>/baseline.json` | JSON: `schema_version` (1), `created_at_utc`, `run_ids[]`, `goldens_sha256`, `prompt_sha256`, `target_model_id`, `judge_prompt_sha256`, `judge_model_id`, `totals.{n,passes,judge_errors}`, `criteria[].{case_id,criterion_index,criterion,n,passes,judge_errors}` | Stage 03 on every later run; committed to git |
| `runs/<ts>/comparison.json` | JSON: `schema_version` (1), `verdict`, `explanation`, `overall.{baseline,candidate}.{passes,n,rate,wilson_95}` and `overall.difference`, `p_value`, `thresholds.{alpha,min_effect,min_samples,max_judge_error_rate}`, `candidate_judge_errors.{errors,rows,rate}`, `cases[]`, `criteria[]` (with `hard_regression`), `unmatched[]` | Stage 04 (report), and any human auditing the verdict |
| Terminal report | The explanation, both rates with their intervals, the p-value, the criteria that fell (`!` marks a hard regression), and the unmatched list | A human on the pull request |
| Process exit code | `0` NO_REGRESSION · `1` REGRESSION · `2` INCONCLUSIVE · `3` bad baseline, run directory or config | CI |

Exit codes 1 and 3 are kept apart deliberately: CI must be able to tell "the
feature got worse" from "the tool could not run".

## Verify

- `uv run pytest -q` — 364 tests across all three stages, all passing, none
  touching the network.
- `uv run ruff check .` — clean at line-length 100.
- `uv run pytest --cov=regression_detect --cov-report=term-missing -q` — 96%.
- `uv run python scripts/detect.py --baseline baselines/summarizer/baseline.json
  --dry-run` — runs all three stages offline with the canned providers, writes
  `comparison.json`, and exits with the verdict's code.
- The p-values are checked in `tests/test_compare.py` against a slow reference
  implementation written inside the test file in exact rational arithmetic, not
  against the implementation itself; the Wilson bounds are checked against
  hand-computed constants.
- After a real run: `comparison.json`'s `overall.candidate.n` plus
  `candidate_judge_errors.errors` equals the candidate's judged criteria; the
  baseline's `goldens_sha256` equals the candidate run manifest's.
- Evidence that the stage worked is a human reading the explanation and agreeing
  it follows from the numbers beside it. This stage grades neither stage 01 nor
  stage 02; it does arithmetic on what stage 02 recorded.

## Approval

**A `REGRESSION` verdict blocks the merge.** It is not advisory, and it is not
cleared by re-running until a run comes out green — a second run that passes
after a first that failed is two samples, and the honest response is to pool
them and look again, not to keep the one you liked.

A human owns two decisions this stage cannot make:

1. **Re-baselining.** A new baseline is a claim that the current quality is the
   quality worth defending. It happens only through an explicit
   `scripts/baseline.py build` against named run directories, after reading the
   candidate's `judged.md`, and lands as its own reviewed commit. Regenerating
   the baseline inside the pull request that made the check go red erases the
   finding; the stage cannot prevent that, so review must.
2. **Changing a threshold.** `alpha`, `min_effect`, `min_samples` and
   `max_judge_error_rate` live in `regression.toml` so that moving one is a diff
   somebody has to approve. Tuning a threshold in response to a specific verdict
   is the same act as deleting the test.

Blocked without human approval: promoting a run to a baseline, editing
`regression.toml` to change a verdict, and editing `goldens/cases.yaml` in
response to a comparison. The stage performs no external write beyond
`comparison.json` in the candidate's own run directory and, for
`baseline.py build`, the baseline file it was told to write.

## Failure Behavior

| Failure | Behavior |
|---|---|
| `regression.toml` missing, unparseable, or holding an out-of-range value | `ConfigFileError` naming the key; exit 3. Never falls back to built-in thresholds |
| Baseline file missing, not JSON, or malformed | `BaselineInputError`; exit 3. `detect.py` fails here before any model call |
| Baseline `schema_version` is not the one this code reads | `BaselineInputError` naming both versions; exit 3. Never guessed at |
| Candidate run directory missing a manifest or `verdicts.jsonl` | `BaselineInputError` naming the file; exit 3 |
| A `verdicts.jsonl` row is malformed | `BaselineInputError` naming the file and line; exit 3. No partial aggregation is written |
| A run was never judged (`verdicts.jsonl` empty) | `BaselineInputError`; exit 3. An empty candidate is not a 0% pass rate |
| `baseline build` given runs that disagree on goldens, prompt, target model, judge prompt or judge model | `BaselineInputError` naming the manifest key and both runs; exit 2. A baseline may only pool runs that measured the same thing |
| Judge could not grade a criterion (`passed: null`) | Excluded from `n`, counted in `judge_errors`. Never counted as a failure |
| Candidate judge errors above `max_judge_error_rate` | `INCONCLUSIVE`; exit 2. The pass rate is computed from too small a surviving sample to trust |
| Candidate matched n below `min_samples` | `INCONCLUSIVE`; exit 2, unless a hard regression or a significant material drop was already proven |
| Goldens changed, so criteria appear on one side only | Reported in `unmatched` and excluded from every number. Never silently compared or dropped |
| Nothing matched at all | `INCONCLUSIVE`; exit 2, with the explanation saying so |
| Stage 01 or 02 failed on some items inside `detect.py` | Those items are already recorded as failures or skips by their own stages; the comparison runs on what survived and the judge-error rule decides whether that is enough |

No cleanup or rollback is needed: the stage writes one file into a run directory
under gitignored `runs/`, plus the baseline file when explicitly asked to.
Escalation path: an `INCONCLUSIVE` verdict is a tooling problem, not a quality
signal — check the quota, the pacing (`--min-interval-ms 60000/RPM`) and the
sample count before reading anything into the pass rate. A `REGRESSION` whose
per-criterion table shows the drop spread evenly across every case is more likely
a judge or model change than a prompt bug; check `judge_model_id` and
`target_model_id` against the baseline's before blaming the diff.
