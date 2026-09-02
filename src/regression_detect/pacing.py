"""Spread a burst of model calls out over time, deterministically.

Stages 01 and 02 both issue a run of calls back to back — one per sample, one per
criterion — and a provider quota is usually per minute. Bounded retries cannot
ride out a window that long, so the burst is paced here instead of half the
calls coming back as rate-limit errors that are honestly recorded and useless.

Nothing here decides anything. It sleeps, and it says when the next call may
start, so the two stages share one implementation rather than two that drift.
"""

import time


def validate_interval(min_interval_ms: int) -> int:
    """Check a pacing interval at the boundary.

    Raises:
        ValueError: if `min_interval_ms` is not a non-negative integer.
    """
    if isinstance(min_interval_ms, bool) or not isinstance(min_interval_ms, int):
        raise ValueError(
            f"min_interval_ms must be a non-negative integer, got {min_interval_ms!r}"
        )
    if min_interval_ms < 0:
        raise ValueError(
            f"min_interval_ms must be a non-negative integer, got {min_interval_ms!r}"
        )
    return min_interval_ms


def pace(previous_start: float | None, min_interval_ms: int) -> float:
    """Sleep so consecutive calls start at least `min_interval_ms` apart.

    Args:
        previous_start: monotonic timestamp the previous call started at, or
            `None` for the first call, which never waits.
        min_interval_ms: the minimum gap; zero or less disables pacing.

    Returns:
        The monotonic timestamp at which the next call may start.
    """
    now = time.monotonic()
    if previous_start is None or min_interval_ms <= 0:
        return now
    wait = (min_interval_ms / 1000) - (now - previous_start)
    if wait <= 0:
        return now
    time.sleep(wait)
    return time.monotonic()
