# Context router

Layer 1. This file answers "where do I go?" — it maps a task to the stage that
owns it. Read this, then read that stage's `CONTEXT.md`, then read only the
inputs that stage declares.

## Stages

The tool is a pipeline: run the feature, judge the outputs, compare against a
baseline, report the verdict. Each stage is one job with one output.

| Stage | Job | Lives in | Built? |
|---|---|---|---|
| `01_run` | Run every golden case through the target feature and record raw outputs | `stages/01_run/CONTEXT.md`, `src/regression_detect/runner.py`, `scripts/run_goldens.py` | Yes |
| `02_judge` | Grade each recorded output against its criteria with an LLM judge, emitting validated structured scores | `stages/02_judge/CONTEXT.md`, `src/regression_detect/judge_runner.py`, `src/regression_detect/judge/`, `scripts/judge_run.py` | Yes |
| `03_compare` | Compare this run's scores against the committed baseline; decide statistically whether a drop exceeds run-to-run noise | `stages/03_compare/CONTEXT.md`, `src/regression_detect/{baseline,compare,comparison,pipeline}.py`, `scripts/{baseline,compare,detect}.py` | Yes |
| `04_report` | Turn the comparison into a human verdict: PR comment, check status, alert | `stages/04_report/` | No |

Stages 01–03 are implemented. Stage 04 has no directory yet; it arrives with its
own `CONTEXT.md` contract before any of its code is written.

Judge calibration (`src/regression_detect/calibration.py`,
`scripts/calibrate.py`) is not a pipeline stage. It is the check on stage 02:
deterministic code comparing a human's ticks in `review.md` against the judge's
verdicts. Run it before trusting a judge score, never as part of a CI run.

## Shared resources

| Path | Layer | What it is |
|---|---:|---|
| `README.md` | 0 | Workspace identity: what the tool is for and how it works |
| `goldens/README.md` | 3 | The rules a golden case must satisfy — authoritative for dataset review |
| `goldens/cases.yaml` | 3 | The golden dataset itself: inputs plus plain-English pass criteria |
| `src/regression_detect/target/prompts/summarize_v1.md` | 3 | The v1 system prompt for the target feature; the thing regressions are detected in |
| `src/regression_detect/target/config.py` | 3 | Target model id. Model identifiers live here and in `judge/config.py`, nowhere else |
| `src/regression_detect/judge/prompts/judge_v1.md` | 3 | The v1 judge system prompt: how one criterion is graded |
| `src/regression_detect/judge/config.py` | 3 | Judge model id, and why a judge from another model family is preferable |
| `src/regression_detect/providers/` | 3 | The provider seam. Only this package names a model vendor |
| `src/regression_detect/goldens.py` | 3 | Golden dataset loader and its validation rules |
| `regression.toml` | 3 | The thresholds a verdict rests on: `alpha`, `min_effect`, `min_samples`, `max_judge_error_rate`. No model id lives here |
| `baselines/README.md` | 3 | The rules a baseline must satisfy — authoritative before any re-baselining |
| `baselines/<target>/baseline.json` | 3 | The reference scores every pull request is measured against. Committed to git, changed only by a reviewed commit |
| `docs/statistics.md` | 3 | Why stage 03 uses the tests it uses, and what they cannot tell you |
| `runs/<UTC timestamp>/` | 4 | Per-run artifacts. Gitignored, never an input to a later edit of the factory |

## Rules that hold across every stage

- Model output is untrusted input. Validate it at the boundary before anything
  downstream reads it.
- Deterministic work stays in deterministic code. A model is called only where
  judgment is genuinely required — one call per sample in stage 01, one grading
  call per criterion in stage 02.
- No stage grades its own work. Stage 01 produces `review.md` for a human;
  stage 02 grades stage 01's outputs; stage 03 grades neither, it does statistics.
- Thresholds are configuration, not literals. Anything that decides a verdict
  lives in `regression.toml` so that changing it is a diff somebody approves.
- Partial failure is visible: recorded per item, counted in the manifest,
  reflected in the exit code. A failed call never becomes an empty success.
- Secrets live in `.env` and never enter source, prompts, logs, or artifacts.
