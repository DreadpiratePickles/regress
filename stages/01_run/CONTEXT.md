# Stage: 01_run

## Objective

Run every golden case through the target feature and record each raw output,
with the evidence needed to reproduce and grade it.

## Inputs

| Path or source | Layer | Authority | Required | Relevant section |
|---|---:|---|---:|---|
| `goldens/cases.yaml` | 3 | Authoritative | Yes | Whole file: every case is run |
| `goldens/README.md` | 3 | Authoritative | No | Case rules — read when a case looks wrong |
| `src/regression_detect/target/prompts/summarize_v1.md` | 3 | Authoritative | Yes | Whole file: sent as the system prompt |
| `src/regression_detect/target/config.py` | 3 | Authoritative | Yes | `target_model_id()` — the model called |
| `TARGET_MODEL_ID` (environment) | 3 | Override | No | Overrides the default model id |
| `GEMINI_API_KEY` (`.env`, environment) | 3 | Authoritative | Yes, unless `--dry-run` | The provider credential; never logged |
| `--samples` (CLI) | 4 | Operator input | No | Samples per case; default 1 |

## Process

Steps 1–4 and 7–10 are deterministic code. Step 6 is the only model call.

1. Parse and validate CLI arguments; reject a non-positive `--samples`.
2. Build the provider: `FakeProvider` under `--dry-run`, otherwise a Gemini
   provider from `GEMINI_API_KEY`. A missing key fails here with an actionable
   message and never reaches step 6.
3. Load and validate the golden dataset: list shape, required keys, snake_case
   ids, unique ids, non-empty criteria. Any violation aborts before any spend.
4. Hash the dataset file and the prompt file (SHA-256) and create the run
   directory `runs/<UTC timestamp>/`.
5. For each case, for each sample index, validate the ticket at the boundary
   (type, non-whitespace, length ≤ 20,000 characters).
6. **Model call.** Send the system prompt and the ticket — wrapped in `<ticket>`
   delimiters as the user message, never formatted into the system prompt — and
   receive one summary. Transient failures retry at most 3 times with
   exponential backoff and jitter.
7. Validate the reply: a non-empty string after stripping, or the sample is
   recorded as a failure. Model output is untrusted input.
8. Record the sample: output or error, model id, prompt hash, latency. A failed
   sample does not abort the run.
9. Write `outputs.jsonl`, `manifest.json`, and `review.md`.
10. Print the counts and exit non-zero if any sample failed.

## Outputs

| Path | Schema or format | Consumer |
|---|---|---|
| `runs/<UTC timestamp>/outputs.jsonl` | One JSON object per line: `case_id`, `sample_index`, `output` (string or null), `model_id`, `prompt_sha256`, `latency_ms`, `error` (string or null), `error_type` (string or null) | Stage 02 (judge) |
| `runs/<UTC timestamp>/manifest.json` | JSON: `run_id`, `stage`, `started_at_utc`, `finished_at_utc`, `goldens_path`, `goldens_sha256`, `prompt_path`, `prompt_sha256`, `model_id`, `provider_class`, `temperature`, `samples`, `case_count`, `counts.{ok,failed,total}` | Stage 03 (compare), and any human auditing provenance |
| `runs/<UTC timestamp>/review.md` | Markdown: per case the id, tags, input (truncated at 600 characters with a note), output or error, and the criteria as `- [ ]` checkboxes | A human grader |
| Process exit code | `0` all samples ok · `1` at least one sample failed · `2` bad configuration or invalid dataset | CI |

## Verify

- `uv run pytest -q` — 204 tests across both stages, all passing, none touching
  the network.
- `uv run ruff check .` — clean at line-length 100.
- `uv run python scripts/run_goldens.py --dry-run` — exits 0 and writes all
  three files with `counts.total == case_count × samples` and `failed == 0`.
- After a real run: `manifest.json` counts equal the line count of
  `outputs.jsonl`; every `case_id` in `outputs.jsonl` exists in the dataset;
  `prompt_sha256` matches `sha256sum` of the prompt file.
- Evidence that the stage worked is a human reading `review.md`, not a green
  test run. This stage grades nothing.

## Approval

A human grader owns the verdict for this stage. They inspect `review.md`,
ticking a criterion only when the output above it satisfies the criterion as
written, and they inspect the manifest counts to confirm nothing was silently
dropped. Blocked without their approval: promoting a run to a baseline,
changing `goldens/cases.yaml` in response to an output, and editing the prompt
to make a case pass. The stage itself performs no external write beyond the
model call and its own run directory.

## Failure Behavior

| Failure | Behavior |
|---|---|
| `GEMINI_API_KEY` missing or rejected | `ProviderConfigError` before any case runs; exit 2; message names the variable, never the key |
| Golden dataset missing, unparseable, or invalid | `GoldenDatasetError` naming the offending case; exit 2; no run directory is treated as valid |
| Prompt file missing | `FileNotFoundError`; exit 2. Never falls back to an empty or default prompt |
| Rate limit, timeout, or 5xx on one call | Retried up to 3 attempts, exponential backoff with jitter; still failing, the sample is recorded with `error_type` and the run continues |
| Empty or non-string model reply | `SummaryValidationError`; recorded as a failed sample, never stored as an empty summary |
| Any sample failed | All three files are still written; exit 1 so CI does not read a partial run as a pass |

No cleanup or rollback is needed: a run directory is append-only and
self-contained, and `runs/` is gitignored. Escalation path: a run that fails
every sample is a configuration or provider problem, not a regression — check
the key, the model id, and the provider status before reading the outputs.
