# Model Regression Detection

> Status: stages 01 and 02 built and running against a real model. Stages 03–04
> are designed but not implemented — see `CONTEXT.md`.

A CI tool that answers one question every time a prompt or model changes:
**did this change make the feature worse?** — and answers it before the change
reaches users.

## How it works

1. **Golden dataset** — curated inputs with plain-English pass criteria
   (`goldens/`). Criteria, not expected strings: outputs are non-deterministic.
2. **Trigger** — a prompt or model config changes in a pull request.
3. **Run** — every golden case is sent through the target feature N times.
4. **Judge** — an LLM grades each output against its criteria, one criterion per
   call, returning structured scores that are validated like any untrusted
   input. The judge itself is calibrated against human labels before it is
   trusted.
5. **Compare** — deterministic code compares the new scores against the
   baseline from `main`, treating the difference as a statistics problem:
   a drop is a regression only when it exceeds run-to-run noise.
6. **Verdict** — a per-case quality diff is posted on the PR; a real regression
   fails the check and alerts Slack.

## What makes this different

- Variance-aware comparison instead of single-run thresholds.
- Judge calibration against human labels, and judge-drift detection.
- No SaaS: baselines live in git, results live in PR comments, runs in your CI.
- The repo guards its own built-in target feature (dogfooding).

## Targets

- `v1` — a built-in support-ticket summarizer (`target/`), used to dogfood.
- `v2` — an adapter for an external open-source LLM app, proving the tool is
  target-agnostic.

## Provider

Model calls (the v1 target and the judge) go through a narrow provider adapter.
The first adapter is Gemini, configured by `GEMINI_API_KEY` in `.env` (see
`.env.example`). Nothing outside the adapter knows which provider is in use.

## Setup

Requires [uv](https://docs.astral.sh/uv/). The Python version is pinned in
`.python-version`; uv installs it for you.

```bash
uv sync                                    # create the venv, install deps
```

Then create a `.env` file in the repository root with your Gemini key:

```
GEMINI_API_KEY=<your key>
```

`.env` is gitignored and must never be committed. Optionally set
`TARGET_MODEL_ID` or `JUDGE_MODEL_ID` to override the default models.

Verify the install, without touching the network or spending anything:

```bash
uv run pytest -q                                          # unit tests
uv run ruff check .                                       # lint
uv run python scripts/run_goldens.py --dry-run            # canned provider
```

Run the goldens against the real model:

```bash
uv run python scripts/run_goldens.py --goldens goldens/cases.yaml --samples 1
```

Outputs land in `runs/<UTC timestamp>/` (gitignored): `outputs.jsonl` for the
next stage, `manifest.json` for provenance, and `review.md` for a human to grade
by hand. Exit code is `0` if every call succeeded, `1` if any call failed, `2`
on bad configuration.

### Judge

Stage 02 grades a finished run, one criterion per model call, and writes four
more files back into the same run directory:

```bash
uv run python scripts/judge_run.py --run runs/<UTC timestamp>
```

`verdicts.jsonl` (one row per criterion), `judge_manifest.json` (provenance and
counts), `scores.json` (pass rates computed by code, not by a model) and
`judged.md` (the same criteria marked ✅ / ❌ / ⚠️ with the judge's reason).
Add `--dry-run` for a canned judge, or `--judge-samples N` to grade each
criterion more than once. Exit code is `0` if every judge call succeeded, `1` if
any failed, `2` on bad configuration — including a `goldens/cases.yaml` whose
hash does not match the one the run was produced from.

The judge makes one call per criterion — several times stage 01's volume — so a
per-minute provider quota bites here first. Pace it with
`--min-interval-ms 60000/RPM` (on the free tier, `--min-interval-ms 6500`).
Without pacing, most criteria come back as `ProviderTransientError` 429s: they
are recorded honestly as unjudged, but they are not scores.

Nothing grades the judge, so calibrate it against a human. Tick the criteria in
that run's `review.md` by hand first — before reading `judged.md`, so the labels
stay independent — then:

```bash
uv run python scripts/calibrate.py --run runs/<UTC timestamp> \
  --graded-cases double_charge_refund,sarcastic_slow_response
```

Only the cases you name are compared; the rest are ungraded, not failed. It
prints the agreement rate, the mismatches, and the two counts that matter:
`false_pass` (the judge passed what you failed — a regression the tool would
miss) and `false_fail` (noise). Nothing here calls a model.

## Layout

```
CONTEXT.md                     Layer 1 router: which stage owns which job
stages/01_run/CONTEXT.md       stage contract for the golden runner
stages/02_judge/CONTEXT.md     stage contract for the judge
goldens/                       golden dataset (cases + criteria) — human-authored
scripts/run_goldens.py         stage 01 entry point
scripts/judge_run.py           stage 02 entry point
scripts/calibrate.py           judge calibration entry point
src/regression_detect/
  goldens.py                   dataset loader + validation
  runner.py                    stage 01: run cases, write run artifacts
  review.py                    renders review.md for the human grader
  judge_runner.py              stage 02: judge each criterion, write verdicts
  judge_inputs.py              reads a stage-01 run back in, validated
  scoring.py                   verdict records, pass-rate arithmetic, judged.md
  calibration.py               human ticks vs judge verdicts — no model call
  providers/                   the provider seam — the only vendor-aware code
    base.py                    Provider protocol + typed errors
    fake.py                    in-memory provider for dry runs and tests
    gemini.py                  Gemini adapter: retries, timeout, error mapping
  judge/                       the grader (one criterion per call)
    criterion.py               message building, strict verdict parsing
    config.py                  judge model id, and the self-preference caveat
    prompts/judge_v1.md        the v1 judge system prompt
  target/                      the feature under test (v1: ticket summarizer)
    summarizer.py              input/output validation, prompt loading
    config.py                  target model id — model names live only here
    prompts/summarize_v1.md    the v1 system prompt
tests/                         pytest suite; no test calls the network
runs/                          per-run artifacts (gitignored)
```

More directories appear as stages are built. Each stage has a `CONTEXT.md`
contract (objective, inputs, process, outputs, verify, approval, failure).
