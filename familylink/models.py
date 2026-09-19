"""Domain models, enums, and typed errors for the Family Link worker."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Operation(str, Enum):
    """Operations the guardian may request. All are human-completed."""

    CREATE = "create"
    LINK = "link"
    MEMBER_STATUS = "member-status"
    CANCEL = "cancel"


class JobState(str, Enum):
    """State-machine states. Persisted so a lost Termux session can recover."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    AWAITING_HUMAN_ACTION = "awaiting_human_action"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobResult(str, Enum):
    """The only results the worker may record. Supplied by a human/approved API."""

    CREATED = "created"
    LINKED = "linked"
    PENDING_HUMAN_ACTION = "pending_human_action"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN_REQUIRES_REVIEW = "unknown_requires_review"


class FailureType(str, Enum):
    """Transient failures may back off & retry. Permanent ones never auto-retry."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


# States from which a job is considered "finished" and immutable-ish.
TERMINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
# Results that count against the family-member capacity.
OCCUPYING_RESULTS = {JobResult.CREATED, JobResult.LINKED}
# States that occupy a "pending" slot for the family head.
PENDING_STATES = {
    JobState.DRAFT,
    JobState.CONFIRMED,
    JobState.AWAITING_HUMAN_ACTION,
    JobState.IN_REVIEW,
}


class FamilyHead(BaseModel):
    id: str
    identifier_fingerprint: str
    label: Optional[str] = None
    created_at: str


class ConsentRecord(BaseModel):
    id: str
    job_id: str
    guardian_name: str
    relationship: Optional[str] = None
    consent_given: bool
    confirmed_at: str


class Job(BaseModel):
    id: str
    idempotency_key: str
    family_head_id: str
    child_display_name: str
    birth_date: str  # ISO date string, exactly as the guardian supplied it
    operation: Operation
    state: JobState
    result: Optional[JobResult] = None
    attempts: int = 0
    max_attempts: int = 5
    next_attempt_at: Optional[str] = None
    deadline_at: Optional[str] = None
    note: Optional[str] = None
    created_at: str
    updated_at: str


class StateTransition(BaseModel):
    id: str
    job_id: str
    from_state: Optional[str]
    to_state: str
    note: Optional[str] = None
    created_at: str


class AuditEvent(BaseModel):
    id: str
    job_id: Optional[str]
    event_type: str
    message: str  # already redacted
    created_at: str


def fingerprint(identifier: str) -> str:
    """Stable, non-reversible fingerprint of a family-head identifier."""
    return hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()


def compute_idempotency_key(
    family_head_id: str,
    child_display_name: str,
    birth_date: str,
    operation: Operation,
) -> str:
    """Deterministic key so a Termux restart cannot create a duplicate job."""
    parts = "|".join(
        [
            family_head_id,
            child_display_name.strip().lower(),
            birth_date.strip(),
            operation.value,
        ]
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


class WorkerError(Exception):
    """Base class for expected, user-facing errors."""


class ValidationError(WorkerError):
    pass


class DuplicateJobError(WorkerError):
    def __init__(self, existing: Job):
        self.existing = existing
        super().__init__(f"An identical job already exists: {existing.id}")


class CapacityError(WorkerError):
    pass


class PendingLimitError(WorkerError):
    pass


class NotFoundError(WorkerError):
    pass


class StateError(WorkerError):
    pass
