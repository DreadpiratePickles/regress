# Baselines

This directory is **committed to git**, unlike `runs/`. That is deliberate and it
is the reason the tool needs no SaaS backend: the baseline is the reference every
pull request is measured against, so it lives in the repository, moves with the
branch, and changes only through a reviewed commit.

```
baselines/summarizer/baseline.json     the v1 target's reference scores
```

## What a baseline file is

Not a pass rate. A pass rate from one run is a coin flip with extra steps. A
baseline is a **per-criterion count**: for every `(case_id, criterion_index,
criterion)`, how many times that criterion was judged (`n`) and how many times it
passed (`passes`), pooled across one or more judged runs. Stage 03 needs the
counts, not the ratio, because Fisher's exact test works on counts.

Each file also records what it measured, and refuses to pool runs that disagree
on any of it:

| Field | Why it must match across the pooled runs | Also checked against the candidate |
|---|---|---|
| `goldens_sha256` | Different criteria are different questions | Yes |
| `prompt_sha256` | The target prompt is the thing under test | **No** — it is expected to differ |
| `target_model_id` | A different model is a different measurement | Yes |
| `judge_prompt_sha256` | A different rubric grades differently | Yes |
| `judge_model_id` | Judges are not interchangeable | Yes |

The last column is stage 03's comparability check. Four of the five fields must
also match the run being compared, and a mismatch is a `ComparabilityError` and
exit 3 — never a verdict, because a run of a different model against an old
baseline is a mistake in the setup, not a regression in the diff. Pin
`TARGET_MODEL_ID` and `JUDGE_MODEL_ID` to the baseline's values, or build a new
baseline for whatever you are measuring now. The target prompt is the deliberate
exception: changing it is the usual reason to run the detector, so both sides'
`prompt_sha256` are recorded in `comparison.json` under `identity` instead.

`run_ids` names the runs it came from and `created_at_utc` says when it was
built. `judge_errors` counts criteria the judge could not grade; they are
excluded from `n` rather than counted as failures.

The shipped `baselines/summarizer/baseline.json` was recorded on
`gemini-3.5-flash-lite` for both target and judge, while the package default is
`gemini-3.6-flash` — so pin both variables when you compare against it.

## Building one

```bash
uv run python scripts/baseline.py build \
  --runs runs/<ts-a> runs/<ts-b> \
  --out baselines/summarizer/baseline.json

uv run python scripts/baseline.py show --baseline baselines/summarizer/baseline.json
```

Pool at least two runs. One run gives every criterion `n = 1`, which cannot tell
a flaky criterion from a stable one, and makes the hard-regression rule (which
needs `n ≥ 2` on both sides) inert.

## Re-baselining

A new baseline is a claim that the current quality is the quality worth
defending, so it is a human decision, never an automatic one:

1. Read the candidate run's `judged.md` and satisfy yourself the change is an
   intended improvement, not a judge artefact or a loosened criterion.
2. Run `baseline.py build` explicitly against the runs you want to pool.
3. Commit the file on its own, with a message saying what changed and why.

Never regenerate a baseline to make a failing check pass. A red stage-03 verdict
that gets erased by a new baseline in the same pull request has measured nothing.
