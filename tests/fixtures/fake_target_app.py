#!/usr/bin/env python
"""A stand-in for "someone else's app", used to test `CommandTarget` for real.

It behaves the way an external text-in/text-out feature behaves — a body on
stdin, an answer on stdout, a non-zero exit and a message on stderr when it
fails — without calling a model, touching the network, or importing anything
from `regression_detect`. That makes the command adapter testable end to end
with `sys.executable` and no API key.

Flags exist only to produce the failure modes the adapter must map to typed
errors:

    --fail      exit 3 after writing a long message to stderr
    --sleep N   stall for N seconds before answering, to trip a timeout
    --silent    exit 0 having written nothing, to trip the empty-output check
    --print-env print the environment variable names it was given, to prove the
                allowlist filtered them
"""

import argparse
import os
import sys
import time

FAIL_EXIT_CODE = 3
STDERR_HEAD_MARKER = "HEAD-MARKER"
STDERR_TAIL_MARKER = "TAIL-MARKER"
STDERR_PADDING_CHARS = 600


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fake_target_app")
    parser.add_argument("--fail", action="store_true", help="Exit non-zero with a long stderr.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to stall first.")
    parser.add_argument("--silent", action="store_true", help="Exit 0 having printed nothing.")
    parser.add_argument("--print-env", action="store_true", help="Print the env var names given.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = sys.stdin.read()

    if args.sleep > 0:
        time.sleep(args.sleep)

    if args.fail:
        sys.stderr.write(
            f"{STDERR_HEAD_MARKER}{'x' * STDERR_PADDING_CHARS}{STDERR_TAIL_MARKER}\n"
        )
        return FAIL_EXIT_CODE

    if args.silent:
        return 0

    if args.print_env:
        print(",".join(sorted(os.environ)))
        return 0

    print(text.strip().upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
