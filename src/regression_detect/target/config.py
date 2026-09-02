"""Configuration for the target feature.

Model identifiers live here and nowhere else. No other module in the package
names a model; logic reads `target_model_id()` instead.
"""

import os

DEFAULT_TARGET_MODEL_ID = "gemini-3.6-flash"
"""Cheap, fast default. Change here, not at any call site.

Chosen because the API retired `gemini-2.5-flash` for new keys and names this
model as its replacement ("This model models/gemini-2.5-flash is no longer
available to new users. Please update your code to use models/gemini-3.6-flash").
`gemini-3.5-flash-lite` is the cheaper alternative and also works; it is not the
default because a weaker target model makes the golden scores noisier, which is
the opposite of what a regression detector wants.

Changing this value changes what the goldens measure. A baseline recorded under
one model id is not comparable to a run under another.
"""

TARGET_MODEL_ID_ENV_VAR = "TARGET_MODEL_ID"


def target_model_id() -> str:
    """The model the target feature runs on, overridable by environment."""
    override = os.environ.get(TARGET_MODEL_ID_ENV_VAR, "").strip()
    return override or DEFAULT_TARGET_MODEL_ID
