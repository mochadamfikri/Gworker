"""Scheduler policy + failure handling: transient backoff vs permanent stop."""

from datetime import datetime, timedelta, timezone

import pytest

from familylink.models import FailureType, JobResult, JobState, Operation
from familylink.scheduler import (
    compute_backoff,
    next_attempt_time,
    should_retry,
    within_time_window,
)


def test_backoff_is_exponential_and_capped():
    assert compute_backoff(1, 2.0, 100.0) == 2.0
    assert compute_backoff(2, 2.0, 100.0) == 4.0
    assert compute_backoff(3, 2.0, 100.0) == 8.0
    assert compute_backoff(10, 2.0, 100.0) == 100.0  # capped


def test_next_attempt_in_future():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    nxt = next_attempt_time(1, 2.0, 100.0, now=now)
    assert nxt > now


def test_time_window():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    created = now - timedelta(seconds=10)
    assert within_time_window(created, 3600, now=now) is True
    assert within_time_window(now - timedelta(seconds=4000), 3600, now=now) is False


def test_should_retry_bounds():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    created = now - timedelta(seconds=1)
    assert should_retry(1, 3, created, 3600, now=now) is True
    assert should_retry(3, 3, created, 3600, now=now) is False  # attempts exhausted
    old = now - timedelta(seconds=99999)
    assert should_retry(1, 3, old, 3600, now=now) is False  # window exceeded


def _create(service, family_head):
    return service.create_job(
        family_head_id=family_head.id,
        child_display_name="Alice",
        birth_date_raw="2015-04-23",
        operation=Operation.CREATE,
        guardian_name="Bob",
        consent_given=True,
    )


def test_permanent_failure_never_retries(service, family_head):
    job = _create(service, family_head)
    out = service.record_failure(job.id, FailureType.PERMANENT, "phone number rejected")
    assert out.state == JobState.FAILED
    assert out.result == JobResult.REJECTED
    assert out.next_attempt_at is None


def test_transient_failure_backs_off_then_escalates(service, family_head):
    job = _create(service, family_head)  # max_attempts=3 from fixture config
    a1 = service.record_failure(job.id, FailureType.TRANSIENT, "network")
    assert a1.attempts == 1 and a1.next_attempt_at is not None and a1.state != JobState.FAILED
    a2 = service.record_failure(job.id, FailureType.TRANSIENT, "network")
    assert a2.attempts == 2 and a2.next_attempt_at is not None
    a3 = service.record_failure(job.id, FailureType.TRANSIENT, "network")
    # Third attempt hits max_attempts -> escalate to human review, no auto-retry.
    assert a3.attempts == 3
    assert a3.state == JobState.IN_REVIEW
    assert a3.result == JobResult.UNKNOWN_REQUIRES_REVIEW
    assert a3.next_attempt_at is None


def test_permanently_failed_cannot_resume(service, family_head):
    from familylink.models import StateError

    job = _create(service, family_head)
    service.record_failure(job.id, FailureType.PERMANENT, "rejected")
    with pytest.raises(StateError):
        service.resume_job(job.id, reviewer_acknowledged=True)


def test_resume_requires_human_review(service, family_head):
    from familylink.models import StateError

    job = _create(service, family_head)
    # exhaust to IN_REVIEW
    for _ in range(3):
        service.record_failure(job.id, FailureType.TRANSIENT, "network")
    with pytest.raises(StateError):
        service.resume_job(job.id, reviewer_acknowledged=False)
    resumed = service.resume_job(job.id, reviewer_acknowledged=True)
    assert resumed.state == JobState.AWAITING_HUMAN_ACTION
