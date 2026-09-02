# Model Regression Detection

> Status: design phase. Nothing runs yet.

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

## Layout

```
goldens/     golden dataset (cases + criteria) — human-authored
target/      the feature under test (v1: ticket summarizer)
```

More directories appear as stages are built. Each stage has a `CONTEXT.md`
contract (objective, inputs, process, outputs, verify, approval, failure).
