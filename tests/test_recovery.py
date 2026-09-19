"""Interrupted-session recovery, cancel, audit redaction, workflow handoff."""

from familylink.models import (
    FailureType,
    JobResult,
    JobState,
    Operation,
)
from familylink.redact import mask_email, mask_name, redact
from familylink.workflow import build_handoff


def _create(service, family_head, name="Alice"):
    return service.create_job(
        family_head_id=family_head.id,
        child_display_name=name,
        birth_date_raw="2015-04-23",
        operation=Operation.CREATE,
        guardian_name="Bob Parent",
        consent_given=True,
    )


def test_interrupted_session_recovers_from_state_machine(service, store, config, family_head):
    from familylink.service import FamilyLinkService
    from familylink.storage.store import Store

    job = _create(service, family_head)
    service.confirm_job(job.id)
    # Simulate lost Termux session: close & reopen the DB entirely.
    store.close()
    store2 = Store(config.db_path)
    recovered = store2.get_job(job.id)
    assert recovered is not None
    assert recovered.state == JobState.AWAITING_HUMAN_ACTION
    assert recovered.result == JobResult.PENDING_HUMAN_ACTION
    # Can continue driving it with a fresh service.
    svc2 = FamilyLinkService(store2, config)
    done = svc2.record_result(job.id, JobResult.CREATED)
    assert done.state == JobState.COMPLETED
    store2.close()


def test_cancel_marks_cancelled(service, family_head):
    job = _create(service, family_head)
    out = service.cancel_job(job.id)
    assert out.state == JobState.CANCELLED
    assert out.result == JobResult.CANCELLED


def test_unknown_requires_review_result(service, family_head):
    job = _create(service, family_head)
    service.confirm_job(job.id)
    out = service.record_result(job.id, JobResult.UNKNOWN_REQUIRES_REVIEW)
    assert out.state == JobState.IN_REVIEW


def test_audit_is_redacted(service, store, family_head):
    _create(service, family_head, name="Charlie")
    messages = [e.message for e in store.list_audit()]
    # Child name must not appear in full in the audit log.
    assert not any("Charlie" in m for m in messages)


def test_redaction_helpers():
    assert mask_email("alice@gmail.com") == "a***@g***.com"
    assert mask_name("Charlie") == "C***"
    assert redact("the otp is 123456") == "[redacted: sensitive value withheld]"
    assert redact("code 987654 sent") == "code [redacted] sent"


def test_workflow_handoff_has_official_url_and_steps(config):
    for op in Operation:
        h = build_handoff(config, op)
        assert h.url.startswith("https://")
        assert len(h.steps) >= 2
        assert "official" in h.reminder.lower() or "Google" in h.reminder
