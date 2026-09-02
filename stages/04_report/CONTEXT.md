# Stage: 04_report

## Objective

Turn a finished comparison into something a human acts on: a Markdown report
for the pull request, an optional Slack message, and a CI check that fails only
when a regression was actually proven.

## Inputs

| Path or source | Layer | Authority | Required | Relevant section |
|---|---:|---|---:|---|
| `runs/<ts>/comparison.json` | 4 | Authoritative | Yes | Whole file: the verdict, the sentence, the numbers, the per-criterion rows, and `baseline_source` |
| `runs/<ts>/manifest.json` | 4 | Authoritative | Yes | `model_id`, `prompt_sha256`, `goldens_sha256`, `samples` |
| `runs/<ts>/judge_manifest.json` | 4 | Authoritative | Yes | `judge_model_id`, `judge_prompt_sha256` |
| `runs/<ts>/scores.json` | 4 | Authoritative | Yes | `overall`: the candidate's own tally, including criteria the comparison could not match |
| `runs/<ts>/verdicts.jsonl` | 4 | Authoritative | Yes | Failing rows only: `criterion`, `reason`, `sample_index` |
| `runs/<ts>/outputs.jsonl` | 4 | Authoritative | Yes | The candidate output each failing verdict was about |
| `SLACK_WEBHOOK_URL` (environment) | 3 | Authoritative | No | Only for `alert.py --send`; a credential, never logged |
| `--out`, `--send`, `--always`, `--report-url` (CLI) | 4 | Operator input | No | Where the report goes, and whether an alert is posted |

The baseline file is **not** re-read. `comparison.json` records which baseline
the verdict was measured against, and that recorded identity is what the report
prints. A baseline on disk can have moved on since; a report that named the
current file while showing yesterday's numbers would be a lie with a citation.

`regression.toml` is not an input either. Every threshold the report shows was
already resolved by stage 03 and written into `comparison.json`; re-reading the
config could print thresholds the verdict was not computed with.

## Process

**Every step is deterministic.** No model is called anywhere in this stage. The
same run directory always renders the same report, byte for byte, which is what
makes the pull-request comment reviewable.

1. Read `comparison.json`, check its `schema_version` is the one this code
   renders, and check every key the report prints is present.
2. Read both manifests and `scores.json` for the provenance and the run's own
   tally; read `outputs.jsonl` and `verdicts.jsonl` for the evidence.
3. Collect, per criterion the judge **failed**, the outputs it was looking at
   and the reasons it gave. Passing verdicts are not collected: this section
   exists to show why a criterion fell.
4. Render `report.md`: an H2 title naming the run, the verdict badge, stage 03's
   explanation verbatim, the overall table (both rates, both Wilson intervals,
   the p-value and the thresholds), the criteria that got worse — sorted by the
   size of the drop, hard regressions marked — the criteria that improved, the
   unmatched note, a `<details>` block per regressed criterion, and a provenance
   footer.
5. Write the report into the run directory. `pipeline.detect()` does this
   automatically after the comparison, so a CI run has the comment ready without
   a second command.
6. For an alert: build a Block Kit payload from the same comparison, and then
   pass three gates before anything leaves the machine — `--send` given,
   `SLACK_WEBHOOK_URL` set, and the verdict is `REGRESSION` (or `--always`).
7. In CI: comment the report on the pull request, upload the run directory as an
   artifact, and fail the check on exit code 1 or 3 only.

No absolute path is written into any artifact this stage produces. The report is
a shared document and a developer's home directory is not part of the evidence.

## Outputs

| Path | Schema or format | Consumer |
|---|---|---|
| `runs/<ts>/report.md` | Markdown: title, verdict badge, explanation, overall table, worsened and improved criterion tables, unmatched note, judge-error count, `<details>` evidence per regressed criterion, provenance footer | The pull-request comment, and any human reading the run |
| Slack message | Block Kit JSON: header with the verdict, the explanation, up to five worsened criteria, an optional report link, a provenance context line | Whoever watches the channel |
| `scripts/alert.py` stdout | The exact payload `--send` would post, printed and not sent | A reviewer, before the first real alert is ever configured |
| GitHub check status | Pass on `NO_REGRESSION` and `INCONCLUSIVE`; fail on `REGRESSION` and on a tool failure | The merge button |
| `report.py` exit code | `0` written · `3` the run could not be read | CI |
| `alert.py` exit code | `0` sent or deliberately skipped · `1` the send failed · `2` misconfigured (no webhook) · `3` the run could not be read | CI |

## Verify

- `uv run pytest -q` — 451 tests across all four stages, all passing, none
  touching the network and none sending a message.
- `uv run ruff check .` — clean at line-length 100.
- `uv run pytest --cov=regression_detect --cov-report=term-missing -q` — 97%.
- `uv run python scripts/detect.py --dry-run --baseline baselines/summarizer/baseline.json`
  — writes `report.md` beside `comparison.json`, offline, with no key set.
- `uv run python scripts/report.py --run runs/<ts>` — re-renders the same file
  from the same run; running it twice produces identical bytes.
- `uv run python scripts/alert.py --run runs/<ts>` — prints a payload and sends
  nothing, with or without `SLACK_WEBHOOK_URL` set.
- The worked example: `docs/examples/regressed_report.md` and
  `docs/examples/regressed_comparison.json` are the real output of running the
  detector against `docs/examples/regressed_prompt.md`, a deliberately broken
  copy of the v1 prompt. That is the evidence the tool catches a regression it
  was not tuned to catch.
- Evidence that the stage worked is a human reading the report and agreeing it
  says what `comparison.json` says. This stage grades nothing; it formats.

## Approval

**The Slack message and the pull-request comment are the only things this tool
does that leave the machine, and both are gated.**

- `scripts/alert.py` is a **dry run by default**. Sending needs `--send`, and
  `--send` needs `SLACK_WEBHOOK_URL`; without it the CLI exits 2 with a message
  naming the variable, never its value. Only a `REGRESSION` verdict is sent
  unless `--always` is passed, so a green run never pages anybody.
- The PR comment is written by CI with `pull-requests: write` and nothing more.
  There is **no auto-merge**, no label change, no branch write, and no status
  override: a human still presses the button.
- A missing `GEMINI_API_KEY` posts a short "skipped" comment and exits 0. A
  pull request from a fork must not go red because it cannot see a secret; a
  check that fails for reasons the author cannot fix gets ignored within a month.

A human owns two decisions this stage cannot make:

1. **Whether a REGRESSION is accepted.** The check fails; only a person can
   decide the change is worth it, and the honest route then is a reviewed
   re-baseline commit (see `stages/03_compare/CONTEXT.md`), not a re-run until
   the dice fall differently.
2. **Whether the alert is wired up at all.** Adding `SLACK_WEBHOOK_URL` as a
   repository secret is a deliberate act. Nothing here creates it, and nothing
   here reads it outside a `--send` invocation.

Blocked without human approval: sending an alert from a local machine without
`--send`, adding any workflow permission beyond `pull-requests: write`, and
editing the report to change what a verdict looks like rather than editing the
comparison that produced it.

## Failure Behavior

| Failure | Behavior |
|---|---|
| `comparison.json` missing, unparseable, or not an object | `ReportInputError`; exit 3. The run is not reported on at all |
| `comparison.json` carries an unknown `schema_version` or verdict | `ReportInputError` naming both versions; exit 3. Never guessed at |
| A manifest, `scores.json`, `outputs.jsonl` or `verdicts.jsonl` is missing or malformed | `ReportInputError` naming the file and, for a JSONL file, the line; exit 3 |
| `comparison.json` has no `baseline_source` (written before stage 04 existed) | The footer prints `baseline runs —`. The report is rendered; the gap is shown, not filled in |
| A failing verdict names a case or criterion index the run does not carry | That row contributes no evidence block. The criterion still appears in the table with its counts |
| A regressed criterion has no recorded output or reason | Its `<details>` block is omitted rather than rendered empty |
| `SLACK_WEBHOOK_URL` missing on `--send` | Exit 2 with an actionable message naming the variable. Nothing is sent, and no partial request is made |
| Webhook is not an `https://` URL | `AlertConfigError`; exit 2. A webhook is a credential and does not travel in the clear |
| The payload exceeds the 40,000-byte bound | `AlertConfigError` before any request; exit 2. Link the report rather than inlining it |
| DNS, connection or timeout failure on the send | `AlertTransportError` after a 10-second timeout; exit 1. The message names the error type, never the URL |
| Slack answers with a non-2xx status | `AlertResponseError` carrying the status and a bounded excerpt of the body; exit 1. The URL is never in the message |
| `GEMINI_API_KEY` absent in CI | The regression job comments that the live check was skipped and exits 0. Never a red check for a missing secret |
| `detect.py` exits 2 (INCONCLUSIVE) in CI | The yellow report is commented and the job passes. "We could not tell" is not "it got worse" |
| `detect.py` exits 3 in CI | No comment is posted (there is no report to post) and the job fails as a configuration fault |

No cleanup or rollback is needed for the report: it is one file in a gitignored
run directory, rewritten from scratch each time. A sent Slack message cannot be
unsent — which is exactly why the default is a dry run and why only a proven
regression is ever posted. Escalation path: a report that disagrees with
`comparison.json` is a stage 04 bug and nothing else, because this stage does no
arithmetic; a report that agrees with `comparison.json` and still looks wrong is
a stage 03 question, and `docs/statistics.md` is where that argument is had.
