# Model Regression Detection

> Status: stage 01 built and running against a real model. Stages 02–04 are
> designed but not implemented — see `CONTEXT.md`.

A CI tool that answers one question every time a prompt or model changes:
**did this change make the feature worse?** — and answers it before the change
reaches users.

## How it works

1. **Golden dataset** — curated inputs with plain-English pass criteria
   (`goldens/`). Criteria, not expected strings: outputs are non-deterministic.
2. **Trigger** — a prompt or model config changes in a pull request.
3. **Run** — every golden case is sent through the target feature N times.
4. **Judge** — an LLM grades each output against its criteria, returning
   structured scores that are validated like any untrusted input.
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
`TARGET_MODEL_ID` to override the default model.

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

## Layout

```
CONTEXT.md                     Layer 1 router: which stage owns which job
stages/01_run/CONTEXT.md       stage contract for the golden runner
goldens/                       golden dataset (cases + criteria) — human-authored
scripts/run_goldens.py         stage 01 entry point
src/regression_detect/
  goldens.py                   dataset loader + validation
  runner.py                    stage 01: run cases, write run artifacts
  review.py                    renders review.md for the human grader
  providers/                   the provider seam — the only vendor-aware code
    base.py                    Provider protocol + typed errors
    fake.py                    in-memory provider for dry runs and tests
    gemini.py                  Gemini adapter: retries, timeout, error mapping
  target/                      the feature under test (v1: ticket summarizer)
    summarizer.py              input/output validation, prompt loading
    config.py                  target model id — model names live only here
    prompts/summarize_v1.md    the v1 system prompt
tests/                         pytest suite; no test calls the network
runs/                          per-run artifacts (gitignored)
```

More directories appear as stages are built. Each stage has a `CONTEXT.md`
contract (objective, inputs, process, outputs, verify, approval, failure).
