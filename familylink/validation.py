"""Local validation.

We validate what we *can* know locally (date sanity, duplicates, capacity,
pending limits). We deliberately do **not** hard-code Google's age/eligibility
thresholds: eligibility is decided by Google during the official flow. We only
sanity-check that the guardian-supplied birth date is a real, non-future date,
and we never modify it.
"""

from __future__ import annotations

from datetime import date, datetime

from .config import Config
from .models import (
    CapacityError,
    Job,
    JobState,
    OCCUPYING_RESULTS,
    Operation,
    PENDING_STATES,
    PendingLimitError,
    ValidationError,
    compute_idempotency_key,
)
from .storage.store import Store

_MIN_YEAR = 1900


def validate_birth_date(raw: str) -> str:
    """Validate a ``YYYY-MM-DD`` birth date. Returns the normalized string.

    Rejects impossible dates (e.g. 2020-02-30), future dates, and absurd years.
    The value is *never* silently modified to satisfy eligibility.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValidationError("Birth date is required (format YYYY-MM-DD).")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(
            f"'{raw}' is not a valid calendar date. Use YYYY-MM-DD (e.g. 2015-04-23)."
        )
    if parsed.year < _MIN_YEAR:
        raise ValidationError(f"Birth year {parsed.year} is implausible.")
    if parsed > date.today():
        raise ValidationError("Birth date cannot be in the future.")
    # Re-serialize to a canonical form without changing the actual date.
    return parsed.isoformat()


def check_pending_limit(store: Store, config: Config, family_head_id: str) -> None:
    pending = store.count_jobs_by_state(family_head_id, list(PENDING_STATES))
    if pending >= config.max_pending_jobs:
        raise PendingLimitError(
            f"Pending-job limit reached ({pending}/{config.max_pending_jobs}). "
            "Resolve or cancel an existing job before creating another."
        )


def check_family_capacity(store: Store, config: Config, family_head_id: str, operation: Operation) -> None:
    """Enforce the maximum family-member capacity for create/link operations."""
    if operation not in (Operation.CREATE, Operation.LINK):
        return
    occupied = store.count_jobs_by_result(family_head_id, list(OCCUPYING_RESULTS))
    if occupied >= config.max_family_members:
        raise CapacityError(
            f"Family is at capacity ({occupied}/{config.max_family_members}). "
            "A member must be removed, or Google must be consulted, before adding another child."
        )


def find_duplicate(store: Store, family_head_id: str, child_display_name: str, birth_date: str, operation: Operation) -> Job | None:
    """Idempotency-based duplicate detection."""
    key = compute_idempotency_key(family_head_id, child_display_name, birth_date, operation)
    return store.get_job_by_idempotency_key(key)
