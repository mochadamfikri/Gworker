import asyncio
import json
from pathlib import Path
import shutil
import stat
from uuid import uuid4

import pytest

from antwork.engine import Engine
from antwork.models import BatchInput, SavedCardTopup, Status
from antwork.sessions import SessionVault, safe_resume_url
from antwork.store import Store


def auth_state():
    return {"cookies": [{"name":"SID", "value":"private-google-session", "domain":".google.com",
                         "path":"/", "expires":-1, "httpOnly":True, "secure":True,"sameSite":"Lax"}],
            "origins":[]}


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


def test_encryption_filtering_permissions_and_reopen(tmp_path):
    directory = tmp_path / "sessions"
    vault = SessionVault(directory)
    worker_id = str(uuid4())
    state = auth_state()
    state["cookies"].append({**state["cookies"][0],"domain":"stripe.com","value":"payment-provider-session"})
    state["origins"] = [{"origin":"https://platform.claude.com","localStorage":[{"name":"form","value":"4242424242424242"}]}]
    assert vault.save(worker_id,"one@example.com",state)
    encrypted = vault.path(worker_id).read_bytes()
    for value in [b"private-google-session",b"payment-provider-session",b"4242424242424242",b"one@example.com"]:
        assert value not in encrypted
    assert stat.S_IMODE(vault.path(worker_id).stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "master.key").stat().st_mode) == 0o600
    assert SessionVault(directory).load(worker_id,"one@example.com") == auth_state()
    with pytest.raises(ValueError):
        vault.load(worker_id,"two@example.com")
    second_id = str(uuid4())
    shutil.copyfile(vault.path(worker_id),vault.path(second_id))
    with pytest.raises(ValueError):
        vault.load(second_id,"one@example.com")


def test_missing_key_and_empty_snapshot_do_not_destroy_saved_state(tmp_path):
    vault = SessionVault(tmp_path)
    worker_id = str(uuid4())
    vault.save(worker_id,"one@example.com",auth_state())
    assert not vault.save(worker_id,"one@example.com",{"cookies":[],"origins":[]})
    assert vault.load(worker_id,"one@example.com") == auth_state()
    (tmp_path / "master.key").unlink()
    with pytest.raises(RuntimeError):
        SessionVault(tmp_path)
    assert vault.path(worker_id).exists()


def test_resume_url_is_encrypted_and_provider_scoped(tmp_path):
    vault = SessionVault(tmp_path)
    worker_id = str(uuid4())
    url = "https://inquiry.withpersona.com/verify?inquiry=private-example-token"
    vault.save(worker_id,"one@example.com",auth_state(),resume_url=url)
    assert b"private-example-token" not in vault.path(worker_id).read_bytes()
    assert SessionVault(tmp_path).resume_url(worker_id,"one@example.com") == url
    for bad in ["http://localhost/", "https://withpersona.com.evil.example/", "https://user:pass@withpersona.com/", "https://withpersona.com:8443/", "file:///etc/passwd", "https://bank.test/challenge"]:
        assert safe_resume_url(bad) is None
    vault.save(worker_id,"one@example.com",auth_state(),resume_url="https://unrelated.example/")
    assert vault.resume_url(worker_id,"one@example.com") is None


class Context:
    async def storage_state(self):
        return auth_state()


class Driver:
    outcome = "no_suspend_detected"
    restored = []
    context = None

    async def run(self, job, account, batch, attention):
        self.context = Context()
        return "session_saved_unverified"

    async def maintain(self, job, saved, attention):
        type(self).restored.append(saved)
        self.context = Context()
        if job.action == "open_session":
            await attention(job,"Manual session review")
            return "session_opened"
        return type(self).outcome

    async def close(self):
        pass


async def test_manual_check_after_restart_and_suspension_not_cleared(tmp_path,payload):
    Driver.restored = []
    Driver.outcome = "no_suspend_detected"
    database = str(tmp_path / "db")
    store = Store(database)
    vault = SessionVault(tmp_path / "sessions")
    engine = Engine(store,Driver,vault=vault)
    engine.start(BatchInput(**payload))
    await until(lambda: engine.secrets is None)
    source = next(iter(engine.jobs.values()))
    assert source.session_status == "saved"
    assert not Driver.restored  # No email check in the background.
    await engine.close()
    store.close()

    store = Store(database)
    engine = Engine(store,Driver,vault=SessionVault(tmp_path / "sessions"))
    action_id = engine.enqueue_action(source.id,"check_email")
    await until(lambda: engine.runner.done())
    assert Driver.restored == [auth_state()]
    assert engine.secrets is None
    assert engine.jobs[action_id].result == "no_suspend_detected"
    assert store.get(source.id)["status"] == "completed"
    assert store.get(source.id)["account_status"] == "no_suspend_detected"
    Driver.outcome = "suspended"
    action_id = engine.enqueue_action(source.id,"check_email")
    await until(lambda: engine.runner.done())
    assert engine.jobs[action_id].status == Status.failed
    assert store.get(source.id)["account_status"] == "suspended"
    Driver.outcome = "no_suspend_detected"
    engine.enqueue_action(source.id,"check_email")
    await until(lambda: engine.runner.done())
    assert store.get(source.id)["account_status"] == "suspended"
    await engine.close()
    store.close()


async def test_session_actions_share_limit_and_cannot_overlap(tmp_path,payload):
    store = Store(str(tmp_path / "db"))
    engine = Engine(store,Driver,vault=SessionVault(tmp_path / "sessions"))
    engine.start(BatchInput(**payload))
    await until(lambda: engine.secrets is None)
    sources = list(engine.jobs)
    engine.concurrency = 1
    first = engine.enqueue_action(sources[0],"open_session")
    second = engine.enqueue_action(sources[1],"open_session")
    await until(lambda: engine.jobs[first].status == Status.attention)
    assert engine.jobs[second].status == Status.queued
    with pytest.raises(ValueError):
        engine.enqueue_action(sources[0],"check_email")
    engine.continue_job(first)
    await until(lambda: engine.jobs[second].status == Status.attention)
    await engine.close()
    assert engine.jobs[first].result == "session_opened"
    store.close()


async def test_saved_card_topup_intent_is_idempotent_without_card_secrets(tmp_path,payload):
    class PaymentDriver(Driver):
        submitted = 0
        async def maintain(self,job,saved,attention):
            self.context = Context()
            assert job.action == "check_topup"
            assert job.amount_usd == "5.00" and job.limit_usd == "6.00"
            PaymentDriver.submitted += 1
            return "payment_confirmed"
    store = Store(str(tmp_path / "db"))
    engine = Engine(store,PaymentDriver,vault=SessionVault(tmp_path / "sessions"))
    engine.start(BatchInput(**payload))
    await until(lambda: engine.secrets is None)
    source_id = next(iter(engine.jobs))
    payment = SavedCardTopup(request_id=uuid4(),amount_usd="5.00",limit_usd="6.00")
    first = engine.enqueue_action(source_id,"check_topup",payment)
    assert engine.enqueue_action(source_id,"check_topup",payment) == first
    await until(lambda: engine.runner.done())
    assert PaymentDriver.submitted == 1
    assert engine.jobs[first].result == "payment_confirmed"
    assert engine.enqueue_action(source_id,"check_topup",payment) == first
    assert PaymentDriver.submitted == 1
    changed = payment.model_copy(update={"amount_usd":"7.00"})
    with pytest.raises(ValueError):
        engine.enqueue_action(source_id,"check_topup",changed)
    assert engine.secrets is None
    await engine.close()
    store.close()
