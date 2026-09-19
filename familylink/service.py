"""Service layer: the state machine that CLI and tests both drive.

Keeps the CLI thin and makes every rule unit-testable without a terminal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import Config
from .models import (
    DuplicateJobError,
    FailureType,
    Job,
    JobResult,
    JobState,
    NotFoundError,
    Operation,
    PENDING_STATES,
    StateError,
    ValidationError,
    compute_idempotency_key,
    iso,
    utcnow,
)
from .redact import mask_name, redact
from .scheduler import next_attempt_time, should_retry
from .storage.store import Store
from .validation import (
    check_family_capacity,
    check_pending_limit,
    find_duplicate,
    validate_birth_date,
)

# Result -> state mapping when a human records an outcome.
_RESULT_TO_STATE = {
    JobResult.CREATED: JobState.COMPLETED,
    JobResult.LINKED: JobState.COMPLETED,
    JobResult.PENDING_HUMAN_ACTION: JobState.AWAITING_HUMAN_ACTION,
    JobResult.REJECTED: JobState.FAILED,
    JobResult.CANCELLED: JobState.CANCELLED,
    JobResult.UNKNOWN_REQUIRES_REVIEW: JobState.IN_REVIEW,
}


class FamilyLinkService:
    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config

    # ---- family head --------------------------------------------------
    def register_family_head(self, identifier_enc: str, fingerprint: str, label: Optional[str]):
        head = self.store.add_family_head(identifier_enc, fingerprint, label)
        self.store.add_audit(
            "family_head.login",
            f"Family head registered (fp={fingerprint[:8]}...).",
        )
        return head

    # ---- job creation -------------------------------------------------
    def create_job(
        self,
        family_head_id: str,
        child_display_name: str,
        birth_date_raw: str,
        operation: Operation,
        guardian_name: str,
        consent_given: bool,
        relationship: Optional[str] = None,
    ) -> Job:
        head = self.store.get_family_head(family_head_id)
        if not head:
            raise NotFoundError(f"Family head '{family_head_id}' not found. Run `familylink family-head login`.")

        child_display_name = (child_display_name or "").strip()
        if not child_display_name:
            raise ValidationError("Child display name is required.")
        if not consent_given:
            raise ValidationError(
                "Guardian consent is required. The legal guardian must authorize this action."
            )

        birth_date = validate_birth_date(birth_date_raw)

        # Idempotency / duplicate detection: a Termux restart must not duplicate.
        existing = find_duplicate(self.store, family_head_id, child_display_name, birth_date, operation)
        if existing:
            raise DuplicateJobError(existing)

        # Bounds that we CAN enforce locally.
        check_pending_limit(self.store, self.config, family_head_id)
        check_family_capacity(self.store, self.config, family_head_id, operation)

        now = utcnow()
        deadline = now + timedelta(seconds=self.config.total_time_window_seconds)
        key = compute_idempotency_key(family_head_id, child_display_name, birth_date, operation)
        job = Job(
            id=_new_job_id(),
            idempotency_key=key,
            family_head_id=family_head_id,
            child_display_name=child_display_name,
            birth_date=birth_date,
            operation=operation,
            state=JobState.DRAFT,
            result=None,
            attempts=0,
            max_attempts=self.config.max_attempts,
            next_attempt_at=None,
            deadline_at=iso(deadline),
            note=None,
            created_at=iso(now),
            updated_at=iso(now),
        )
        self.store.insert_job(job)
        self.store.add_consent(job.id, guardian_name, relationship, consent_given)
        self.store.add_transition(job.id, None, JobState.DRAFT.value, "job created")
        self.store.add_audit(
            "job.create",
            redact(
                f"Created {operation.value} job for child {mask_name(child_display_name)} "
                f"(guardian {mask_name(guardian_name)}, consent=yes)."
            ),
            job_id=job.id,
        )
        return job

    # ---- confirmation & handoff --------------------------------------
    def confirm_job(self, job_id: str) -> Job:
        """Move a DRAFT job to AWAITING_HUMAN_ACTION after explicit confirmation."""
        job = self._require_job(job_id)
        if job.state != JobState.DRAFT:
            raise StateError(f"Job {job_id} is '{job.state.value}', expected 'draft' to confirm.")
        self._transition(job, JobState.AWAITING_HUMAN_ACTION, "guardian confirmed data; official-UI handoff")
        job.result = JobResult.PENDING_HUMAN_ACTION
        self.store.update_job(job)
        self.store.add_audit("job.confirm", "Guardian confirmed child data; awaiting official-UI action.", job.id)
        return job

    # ---- recording results -------------------------------------------
    def record_result(self, job_id: str, result: JobResult) -> Job:
        job = self._require_job(job_id)
        if job.state in (JobState.COMPLETED, JobState.CANCELLED):
            raise StateError(f"Job {job_id} is already '{job.state.value}' and cannot change.")
        target = _RESULT_TO_STATE[result]
        self._transition(job, target, f"result recorded: {result.value}")
        job.result = result
        self.store.update_job(job)
        self.store.add_audit("job.result", f"Result recorded: {result.value}.", job.id)
        return job

    # ---- transient/permanent failure handling ------------------------
    def record_failure(self, job_id: str, failure_type: FailureType, reason: str = "") -> Job:
        job = self._require_job(job_id)
        if failure_type == FailureType.PERMANENT:
            self._transition(job, JobState.FAILED, "permanent failure; no auto-retry")
            job.result = JobResult.REJECTED
            job.next_attempt_at = None
            self.store.update_job(job)
            self.store.add_audit("job.failure", redact(f"Permanent failure: {reason}"), job.id)
            return job

        # Transient: bounded exponential backoff, else escalate to human review.
        job.attempts += 1
        created = datetime.fromisoformat(job.created_at)
        if should_retry(job.attempts, job.max_attempts, created, self.config.total_time_window_seconds):
            nxt = next_attempt_time(job.attempts, self.config.backoff_base_seconds, self.config.backoff_cap_seconds)
            job.next_attempt_at = iso(nxt)
            job.note = f"transient failure; retry {job.attempts}/{job.max_attempts}"
            self.store.update_job(job)
            self.store.add_transition(job.id, job.state.value, job.state.value, job.note)
            self.store.add_audit("job.retry", redact(f"Transient failure; scheduled retry: {reason}"), job.id)
        else:
            self._transition(job, JobState.IN_REVIEW, "transient retries exhausted; human review required")
            job.result = JobResult.UNKNOWN_REQUIRES_REVIEW
            job.next_attempt_at = None
            self.store.update_job(job)
            self.store.add_audit("job.review", "Retry budget exhausted; escalated to human review.", job.id)
        return job

    # ---- resume / cancel ---------------------------------------------
    def resume_job(self, job_id: str, reviewer_acknowledged: bool) -> Job:
        job = self._require_job(job_id)
        if job.state == JobState.FAILED:
            raise StateError(
                f"Job {job_id} was permanently rejected and cannot be resumed. Create a new job if appropriate."
            )
        if job.state not in (JobState.IN_REVIEW, JobState.AWAITING_HUMAN_ACTION):
            raise StateError(f"Job {job_id} is '{job.state.value}'; nothing to resume.")
        if not reviewer_acknowledged:
            raise StateError("Human review is required before resuming a failed/paused job.")
        self._transition(job, JobState.AWAITING_HUMAN_ACTION, "resumed after human review")
        job.result = JobResult.PENDING_HUMAN_ACTION
        job.next_attempt_at = None
        self.store.update_job(job)
        self.store.add_audit("job.resume", "Job resumed after human review.", job.id)
        return job

    def cancel_job(self, job_id: str) -> Job:
        job = self._require_job(job_id)
        if job.state in (JobState.COMPLETED, JobState.CANCELLED):
            raise StateError(f"Job {job_id} is already '{job.state.value}'.")
        self._transition(job, JobState.CANCELLED, "cancelled by user")
        job.result = JobResult.CANCELLED
        job.next_attempt_at = None
        self.store.update_job(job)
        self.store.add_audit("job.cancel", "Job cancelled by user.", job.id)
        return job

    # ---- helpers ------------------------------------------------------
    def _require_job(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if not job:
            raise NotFoundError(f"Job '{job_id}' not found.")
        return job

    def _transition(self, job: Job, to_state: JobState, note: str) -> None:
        self.store.add_transition(job.id, job.state.value, to_state.value, note)
        job.state = to_state


def _new_job_id() -> str:
    import uuid

    return "job_" + uuid.uuid4().hex[:12]
