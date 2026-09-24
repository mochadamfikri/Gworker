import asyncio
from uuid import uuid4

import pytest

from antwork.engine import Engine
from antwork.models import BatchInput, Status, TERMINAL
from antwork.store import Store


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


class Driver:
    def __init__(self, tracker):
        self.tracker = tracker

    async def run(self, job, account, batch, attention):
        self.tracker.add(job.id)
        await attention(job, "KYC / 3DS requires review")

    async def close(self):
        pass


@pytest.fixture
def store(tmp_path):
    store = Store(str(tmp_path / "metadata.db"))
    yield store
    store.close()


async def test_concurrency_attention_and_cleanup(store, payload):
    seen = set()
    engine = Engine(store, lambda: Driver(seen))
    engine.start(BatchInput(**payload))
    await until(lambda: sum(j.status == Status.attention for j in engine.jobs.values()) == 2)
    assert len(seen) == 2
    assert sum(j.status == Status.queued for j in engine.jobs.values()) == 2
    first = next(j for j in engine.jobs.values() if j.status == Status.attention)
    first.controller = True
    with pytest.raises(ValueError):
        engine.continue_job(first.id)
    first.controller = False
    engine.continue_job(first.id)
    await until(lambda: len(seen) == 3)
    assert first.status == Status.completed
    assert engine.secrets is not None
    await engine.close()
    assert engine.secrets is None
    assert all(j.status in TERMINAL for j in engine.jobs.values())
    assert all(j.browser is None for j in engine.jobs.values())


async def test_all_success_releases_batch(store, payload):
    class Immediate:
        async def run(self, *args): pass
        async def close(self): pass
    engine = Engine(store, Immediate)
    engine.start(BatchInput(**payload))
    await until(lambda: engine.secrets is None)
    assert all(j.status == Status.completed for j in engine.jobs.values())
    original = engine.batch_id
    assert engine.start(BatchInput(**payload)) == original
    assert engine.secrets is None
    payload["request_id"] = str(uuid4())
    engine.start(BatchInput(**payload))
    await engine.close()


async def test_failure_never_retries_or_persists_secret(store, payload, tmp_path):
    calls = []
    class Failure:
        async def run(self, job, account, batch, attention):
            calls.append(job.id)
            raise RuntimeError(account.password.get_secret_value() + batch.card.number.get_secret_value())
        async def close(self): pass
    engine = Engine(store, Failure)
    engine.start(BatchInput(**payload))
    await until(lambda: engine.secrets is None)
    assert len(calls) == 4
    assert all(j.status == Status.failed for j in engine.jobs.values())
    data = (tmp_path / "metadata.db").read_bytes()
    for value in [b"test:password", b"4242424242424242", b"Test Street 1", b"cvv"]:
        assert value not in data
    await engine.close()


async def test_lower_concurrency_preserves_running_workers(store, payload):
    engine = Engine(store, lambda: Driver(set()))
    engine.start(BatchInput(**payload))
    await until(lambda: sum(j.status == Status.attention for j in engine.jobs.values()) == 2)
    engine.concurrency = 1
    running = [j for j in engine.jobs.values() if j.status == Status.attention]
    await engine.cancel(running[0].id)
    await asyncio.sleep(.03)
    assert sum(j.status == Status.queued for j in engine.jobs.values()) == 2
    await engine.close()


async def test_attention_timeout_closes_browser(store, payload):
    engine = Engine(store, lambda: Driver(set()), attention_timeout=.01)
    engine.start(BatchInput(**payload))
    await until(lambda: engine.secrets is None)
    assert all(j.status == Status.failed and j.browser is None for j in engine.jobs.values())
    await engine.close()


async def test_duplicate_batch_and_limit(store, payload):
    engine = Engine(store, lambda: Driver(set()), max_concurrency=1)
    with pytest.raises(ValueError):
        engine.start(BatchInput(**payload))
    payload["concurrency"] = 1
    engine.start(BatchInput(**payload))
    payload["request_id"] = str(uuid4())
    with pytest.raises(ValueError):
        engine.start(BatchInput(**payload))
    await engine.close()


def test_restart_interrupts_work_without_replay(tmp_path):
    from antwork.engine import Job
    path = str(tmp_path / "restart.db")
    store = Store(path)
    store.put(Job("job", "batch", "test@example.com", status=Status.attention))
    store.close()
    store = Store(path)
    assert store.list()[0]["status"] == "interrupted"
    store.close()
