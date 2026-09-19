"""Job creation, duplicate/idempotency, capacity, pending-limit, consent tests."""

import pytest

from familylink.models import (
    CapacityError,
    DuplicateJobError,
    JobResult,
    JobState,
    Operation,
    PendingLimitError,
    ValidationError,
)


def _create(service, family_head, name="Alice", bd="2015-04-23", op=Operation.CREATE):
    return service.create_job(
        family_head_id=family_head.id,
        child_display_name=name,
        birth_date_raw=bd,
        operation=op,
        guardian_name="Bob Parent",
        consent_given=True,
        relationship="father",
    )


def test_consent_required(service, family_head):
    with pytest.raises(ValidationError):
        service.create_job(
            family_head_id=family_head.id,
            child_display_name="Alice",
            birth_date_raw="2015-04-23",
            operation=Operation.CREATE,
            guardian_name="Bob",
            consent_given=False,
        )


def test_create_job_records_consent_and_transition(service, store, family_head):
    job = _create(service, family_head)
    assert job.state == JobState.DRAFT
    consent = store.get_consent(job.id)
    assert consent is not None and consent.consent_given is True
    assert len(store.list_transitions(job.id)) == 1


def test_duplicate_is_idempotent(service, family_head):
    _create(service, family_head)
    with pytest.raises(DuplicateJobError):
        _create(service, family_head)


def test_idempotency_key_stable_across_restart(service, store, config, family_head):
    from familylink.service import FamilyLinkService

    job = _create(service, family_head)
    # Simulate a Termux restart: brand new service instance, same DB.
    service2 = FamilyLinkService(store, config)
    with pytest.raises(DuplicateJobError):
        service2.create_job(
            family_head_id=family_head.id,
            child_display_name="Alice",
            birth_date_raw="2015-04-23",
            operation=Operation.CREATE,
            guardian_name="Bob",
            consent_given=True,
        )
    assert len(store.list_jobs()) == 1
    assert store.get_job(job.id) is not None


def test_pending_limit_enforced(service, family_head):
    _create(service, family_head, name="A", bd="2015-01-01")
    _create(service, family_head, name="B", bd="2015-01-02")
    with pytest.raises(PendingLimitError):
        _create(service, family_head, name="C", bd="2015-01-03")


def test_family_capacity_enforced(service, store, family_head):
    # Fill capacity (max_family_members=3) with LINKED results.
    for i in range(3):
        job = _create(service, family_head, name=f"Child{i}", bd=f"2015-01-0{i+1}")
        service.record_result(job.id, JobResult.LINKED)
    with pytest.raises(CapacityError):
        _create(service, family_head, name="Overflow", bd="2016-01-01")
