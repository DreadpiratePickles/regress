#!/usr/bin/env python
"""Somebody else's ticket summarizer. Nothing here knows the detector exists.

This file is deliberately a stranger to the rest of the repository. It imports
no `regression_detect` module, shares no prompt, no provider adapter, no model
id and no validation code with the tool that measures it. It is here so the
claim "the detector can regression-test a feature it did not write" can be
checked rather than asserted.

The contract it exposes is the whole contract the command adapter needs:

    a ticket on stdin  →  a summary on stdout  →  exit 0
    anything went wrong  →  a message on stderr  →  exit non-zero

Run it by hand:

    echo "My order never arrived." | uv run python \\
        examples/external_target/ticket_summarizer_app.py

Run the goldens through it:

    uv run python scripts/run_goldens.py \\
        --config examples/external_target/regression.external.toml --samples 1

It needs `GEMINI_API_KEY`, from the environment or from a `.env` beside it, and
it prints only the summary so its stdout can be piped anywhere.
"""

import os
import sys

CONFIG_ERROR_EXIT_CODE = 2
CALL_FAILED_EXIT_CODE = 1
MAX_TICKET_CHARS = 20_000
REQUEST_TIMEOUT_MS = 60_000
DEFAULT_MODEL_ID = "gemini-3.5-flash-lite"
MODEL_ID_ENV_VAR = "TICKET_SUMMARIZER_MODEL_ID"
API_KEY_ENV_VAR = "GEMINI_API_KEY"

SYSTEM_PROMPT = """\
You are a support triage assistant. You are given one customer support ticket.

Write a summary for the support agent who will pick the ticket up next. Cover, \
in this order and in at most six sentences:

1. What the customer says happened, in plain language.
2. What they are asking for.
3. Any account numbers, order numbers, dates or amounts they gave, exactly as \
written.
4. How upset they sound, if it is obvious from the wording.

Report only what the ticket says. Do not guess at causes, do not propose a fix, \
and do not invent details the customer did not give. If something the agent \
would want is missing from the ticket, say that it is missing.
"""


def fail(message: str, code: int) -> int:
    print(f"ticket_summarizer: {message}", file=sys.stderr)
    return code


def summarize(ticket: str, *, api_key: str, model_id: str) -> str:
    """Call the model. Raises whatever the SDK raises; `main` turns that into an exit code."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)
    )
    response = client.models.generate_content(
        model=model_id,
        contents=f"<ticket>\n{ticket}\n</ticket>",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, temperature=0.2
        ),
    )
    return (getattr(response, "text", None) or "").strip()


def main() -> int:
    ticket = sys.stdin.read()
    if not ticket.strip():
        return fail("no ticket on stdin", CONFIG_ERROR_EXIT_CODE)
    if len(ticket) > MAX_TICKET_CHARS:
        return fail(
            f"ticket is {len(ticket)} characters, the limit is {MAX_TICKET_CHARS}",
            CONFIG_ERROR_EXIT_CODE,
        )

    try:  # a .env beside the app is a convenience, not a requirement
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        return fail(f"{API_KEY_ENV_VAR} is not set", CONFIG_ERROR_EXIT_CODE)
    model_id = os.environ.get(MODEL_ID_ENV_VAR, "").strip() or DEFAULT_MODEL_ID

    try:
        summary = summarize(ticket, api_key=api_key, model_id=model_id)
    except ImportError:
        return fail("the google-genai SDK is not installed", CONFIG_ERROR_EXIT_CODE)
    except Exception as exc:  # the SDK raises a wide range; the exit code is what matters
        return fail(f"the {model_id} call failed: {type(exc).__name__}", CALL_FAILED_EXIT_CODE)

    if not summary:
        return fail(f"{model_id} returned nothing", CALL_FAILED_EXIT_CODE)

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
