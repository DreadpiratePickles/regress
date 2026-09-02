"""The v1 target feature: a support-ticket summarizer, used to dogfood the tool."""

from .config import DEFAULT_TARGET_MODEL_ID, target_model_id
from .summarizer import (
    DEFAULT_PROMPT_PATH,
    MAX_TICKET_CHARS,
    InvalidTicketError,
    SummarizerError,
    SummaryValidationError,
    TicketTooLongError,
    summarize,
)

__all__ = [
    "DEFAULT_PROMPT_PATH",
    "DEFAULT_TARGET_MODEL_ID",
    "InvalidTicketError",
    "MAX_TICKET_CHARS",
    "SummarizerError",
    "SummaryValidationError",
    "TicketTooLongError",
    "summarize",
    "target_model_id",
]
