"""Bounded job scheduler.

Replaces JioFarm's ``RefundWorker``. The core Family Link flow is human-driven,
so this scheduler governs only the worker's *own* retryable steps (e.g. an
optional guardian notification). Policy:

* Permanent rejection -> never auto-retry; go to FAILED.
* Transient failure -> exponential backoff, bounded by ``max_attempts`` and a
  total time window. When bounds are hit, escalate to IN_REVIEW (human required).
* A job in IN_REVIEW / FAILED can only continue after an explicit human review.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def compute_backoff(attempt: int, base_seconds: float, cap_seconds: float) -> float:
    """Exponential backoff for transient failures only.

    ``attempt`` is 1-based (first retry = 1). Result is capped.
    """
    if attempt < 1:
        attempt = 1
    delay = base_seconds * (2 ** (attempt - 1))
    return min(delay, cap_seconds)


def next_attempt_time(
    attempt: int, base_seconds: float, cap_seconds: float, now: Optional[datetime] = None
) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=compute_backoff(attempt, base_seconds, cap_seconds))


def within_time_window(created_at: datetime, total_window_seconds: int, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return (now - created_at).total_seconds() <= total_window_seconds


def should_retry(
    attempts: int,
    max_attempts: int,
    created_at: datetime,
    total_window_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    """Whether a transient failure is still eligible for another bounded retry."""
    if attempts >= max_attempts:
        return False
    return within_time_window(created_at, total_window_seconds, now)
