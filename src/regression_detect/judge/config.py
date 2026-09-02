"""Configuration for the judge.

Model identifiers live here and in `target/config.py`, and nowhere else.

The judge currently defaults to the same model as the target feature, because
only one provider key is available in this workspace. That is a compromise, not
a design choice: a model asked to grade its own output exhibits
**self-preference bias** — it scores text it produced (or that its own family
produced, in its own house style) more generously than text from another model.
A judge sharing the target's weights therefore reports a pass rate that is
biased upward, and worse, the bias moves with the target: change the target
model and the judge's leniency changes with it, which is exactly the confound a
regression detector must not have.

As soon as a second provider key exists, set `JUDGE_MODEL_ID` to a model from a
different family than `target_model_id()` and keep it pinned there across
baseline and candidate runs. Until then, the calibration stage
(`calibration.py`) is what keeps this honest: human labels on `review.md` are
compared against the judge's verdicts, and a rising false-pass count is the
symptom this bias produces.
"""

import os

from ..target.config import DEFAULT_TARGET_MODEL_ID

DEFAULT_JUDGE_MODEL_ID = DEFAULT_TARGET_MODEL_ID
"""Read from the target's default rather than restated, so the two cannot drift
apart silently while they are deliberately the same model."""

JUDGE_MODEL_ID_ENV_VAR = "JUDGE_MODEL_ID"


def judge_model_id() -> str:
    """The model the judge runs on, overridable by environment."""
    override = os.environ.get(JUDGE_MODEL_ID_ENV_VAR, "").strip()
    return override or DEFAULT_JUDGE_MODEL_ID
