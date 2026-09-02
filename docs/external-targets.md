# Pointing the detector at your own feature

The built-in ticket summarizer is what this repository dogfoods, not what the
tool is for. Everything stage 01 needs from a feature under test is:

```python
class Target(Protocol):
    target_id: str
    def run(self, input_text: str) -> str: ...
    def provenance(self) -> dict[str, str]: ...
```

Text in, text out, plus enough identity to say which feature produced a run.
Three adapters implement it, chosen by the `[target]` section of a config file.
Everything downstream — the judge, the statistics, the report, the alert — is
unchanged, because none of it ever knew what a summarizer was.

## The three kinds

Every `[target]` block below is a fragment, not a config file on its own:
`load_config` also requires `[compare]`, `[run]` and `[models]`. The complete,
loadable example is
[`examples/external_target/regression.external.toml`](../examples/external_target/regression.external.toml)
— copy it and replace its `[target]` section with one of these.

### `builtin` — the packaged summarizer

The default, and what a config with no `[target]` section means.

```toml
[target]
kind = "builtin"
# optional:
# prompt_path = "src/regression_detect/target/prompts/summarize_v1.md"
# temperature = 0.2
```

When stage 01 or `detect.py` is given `--prompt` or `--temperature`, those win
over the config: varying the prompt is the entire reason a pull request runs
this tool.

### `command` — any program, stdin to stdout

The general case, and the one to reach for first. Most text-in/text-out features
can be exposed as a program that reads stdin and prints to stdout; a program
needs no import of this package, no shared runtime, and no cooperation beyond an
exit code.

```toml
[target]
kind = "command"
argv = ["uv", "run", "python", "examples/external_target/ticket_summarizer_app.py"]
timeout_s = 120.0
env_allowlist = ["GEMINI_API_KEY", "HOME"]
# optional: cwd = "/path/to/your/app"
```

The contract with your app:

| Your app does | The adapter records |
|---|---|
| prints an answer, exits 0 | the stripped stdout, as the sample's output |
| exits non-zero | `TargetExecutionError` with the exit code and the tail of stderr |
| answers too slowly | `TargetTimeoutError` |
| exits 0 having printed nothing | `TargetResponseError` |
| cannot be started at all | `TargetExecutionError` naming the program |

A failure is a recorded sample with an `error_type`, not a crashed run — the
same partial-failure behaviour stage 01 has always had.

`--dry-run` never starts your program. For any non-builtin kind a dry run
substitutes a canned in-memory target, so it costs nothing and touches nothing,
and the run records `target_id = "fake:dry-run"` so its numbers can never be
mistaken for a measurement of your feature.

`examples/external_target/ticket_summarizer_app.py` is a worked example: a
standalone script with its own prompt, its own model id and its own error
handling, which imports nothing from `regression_detect`. Run the goldens
through it with:

```bash
uv run python scripts/run_goldens.py \
  --config examples/external_target/regression.external.toml \
  --samples 1 --min-interval-ms 6500
```

### `http` — a JSON endpoint

For a feature that already runs as a service.

```toml
[target]
kind = "http"
url = "https://api.example.com/v1/summarize"
timeout_s = 30.0
input_field = "ticket"        # POSTed as {"ticket": "<the case input>"}
output_field = "summary"      # read back from {"summary": "..."}
auth_header_env = "MY_APP_TOKEN"   # the NAME of a variable, never a token
```

The request is `POST {input_field: text}` as JSON. The response must have a 2xx
status, be under 1 MiB, parse as a JSON object, and carry a non-empty string at
`output_field`; anything else is a typed error rather than an empty-string
success. A response is untrusted input like any other.

## What provenance is recorded

Every run's `manifest.json` gains two keys: `target_id`, and a `target` block
holding whatever the adapter reports.

| Kind | Recorded |
|---|---|
| `builtin` | `model_id`, `provider_class`, `prompt_path`, `prompt_sha256`, `temperature` |
| `command` | the full `argv`, `argv_sha256`, `timeout_s`, `cwd`, `env_allowlist` |
| `http` | `url`, `timeout_s`, `input_field`, `output_field`, `auth_header_env` (the variable's *name*) |

No adapter ever records a secret. The http adapter records which variable holds
its bearer token and never the token's value, in provenance, in a log line, or in
an error message.

The manifest's long-standing `prompt_sha256` and `model_id` keys are still
there, and for a builtin run they hold exactly what they always held — which is
why moving stage 01 onto this seam changed no hash in `baselines/`. For any
other target there is no prompt file and no model id to record, so
`prompt_sha256` holds the SHA-256 of the whole provenance block and `model_id`
holds the `target_id`. Both are still one stable string per target identity,
which is all the rest of the pipeline ever wanted from them.

## A target change invalidates the baseline

A baseline is a statement about one feature: *this criterion passed 125 times out
of 134 for this prompt, on this model.* Measured against a different feature, the
same numbers answer a question nobody asked. Comparing them would not be a
stricter test, it would be a meaningless one — the difference between the two
pass rates is the difference between two features, not a regression in either.

So: **build a new baseline per target.** The identity fields make this visible
rather than silent — a run through the external example records
`model_id = "command:fdd00b9726fe751c"` where the summarizer baseline records
`gemini-3.5-flash-lite`.

```bash
# two judged runs of YOUR target, then a baseline of your own
uv run python scripts/baseline.py build \
  --runs runs/<ts-a> runs/<ts-b> \
  --out baselines/<your-target>/baseline.json
```

Pool at least two runs, for the reason `baselines/README.md` gives: one run
leaves every criterion at `n = 1`, which cannot tell a flaky criterion from a
stable one.

The same rule applies within one target. Change the prompt, the model, or the
argv, and the identity hash changes with it.

## Security notes

The adapters run code and talk to services on your behalf, so the defaults are
the careful ones.

- **argv is a list, never a shell string.** `subprocess.run` is called with an
  argument list and `shell=False`, always. No command is ever built by
  concatenating strings, so a golden case's text, a path, or a config value can
  never become a command. `argv = "python app.py | tee log"` is a config error,
  not a pipeline.
- **The child's environment is an allowlist.** A command target's subprocess
  gets `PATH` plus the variables `env_allowlist` names, and nothing else. It does
  not inherit every credential the parent happens to hold; a target that needs an
  API key has to say so.
- **Bearer tokens come from the environment by name.** `auth_header_env` names a
  variable; the value is read at call time from that variable, which belongs in
  `.env`. A token never appears in a config file, in provenance, or in an error
  message. A named-but-unset variable fails before the request is made, not
  after.
- **Everything is bounded.** Every target has a timeout. An http response is
  capped at 1 MiB before it is parsed. An input is capped at 20,000 characters
  before it is sent.
- **Diagnostics never carry the input.** A failing command's stderr is quoted
  back at most 500 characters, tail-first — the case input that produced it is
  not, because a case input is customer text and an error message travels
  further than a run directory.
