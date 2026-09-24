import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from .models import BatchInput, Status, TERMINAL


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    batch_id: str
    email: str
    status: Status = Status.queued
    phase: str = "Menunggu slot worker"
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    resume: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    browser: object = field(default=None, repr=False)
    task: object = field(default=None, repr=False)
    controller: bool = False


class Engine:
    def __init__(self, store, driver_factory, max_concurrency=5, attention_timeout=900):
        self.store = store
        self.driver_factory = driver_factory
        self.max_concurrency = max_concurrency
        self.attention_timeout = attention_timeout
        self.jobs = {}
        self.secrets = None
        self.batch_id = None
        self.concurrency = 3
        self.wakeup = asyncio.Event()
        self.runner = None
        self.closing = False

    def update(self, job, status, phase):
        job.status, job.phase, job.updated = status, phase, now()
        self.store.put(job)
        self.wakeup.set()

    def start(self, payload: BatchInput):
        existing = self.store.find_request(payload.request_id)
        if existing:
            return existing
        if self.secrets is not None:
            raise ValueError("Masih ada batch aktif")
        if payload.concurrency > self.max_concurrency:
            raise ValueError("Concurrency melebihi batas server")
        self.batch_id = str(uuid4())
        self.store.record_request(payload.request_id, self.batch_id)
        self.secrets = payload
        self.concurrency = payload.concurrency
        self.jobs = {}
        for account in payload.accounts:
            job = Job(str(uuid4()), self.batch_id, account.email)
            self.jobs[job.id] = job
            self.store.put(job)
        self.runner = asyncio.create_task(self._schedule())
        return self.batch_id

    async def _schedule(self):
        try:
            while not self.closing:
                self.wakeup.clear()
                active = sum(j.task is not None and not j.task.done() for j in self.jobs.values())
                for job in self.jobs.values():
                    if active >= self.concurrency:
                        break
                    if job.status == Status.queued:
                        self.update(job, Status.running, "Membuka browser")
                        job.task = asyncio.create_task(self._run(job))
                        active += 1
                if all(j.status in TERMINAL and (j.task is None or j.task.done())
                       for j in self.jobs.values()):
                    break
                await self.wakeup.wait()
        finally:
            self.secrets = None

    async def _run(self, job):
        driver = None
        try:
            account = next(a for a in self.secrets.accounts if a.email == job.email)
            driver = self.driver_factory()
            job.browser = driver
            await driver.run(job, account, self.secrets, self.attention)
            self.update(job, Status.completed, "Pembelian saldo terkonfirmasi")
        except asyncio.CancelledError:
            self.update(job, Status.cancelled, "Dibatalkan; cek transaksi terakhir sebelum mengulang")
        except TimeoutError:
            self.update(job, Status.failed, "Waktu verifikasi habis; tidak dicoba ulang otomatis")
        except Exception:
            # Playwright exceptions can contain passwords, URLs, or DOM text.
            self.update(job, Status.failed, "Proses gagal; periksa akun sebelum mencoba lagi")
        finally:
            if driver:
                try:
                    await driver.close()
                except Exception:
                    pass
            job.browser = None
            job.controller = False
            # Wake after this task has actually become done.
            asyncio.get_running_loop().call_soon(self.wakeup.set)

    async def attention(self, job, message):
        job.resume.clear()
        self.update(job, Status.attention, message)
        await asyncio.wait_for(job.resume.wait(), self.attention_timeout)
        self.update(job, Status.running, "Memeriksa hasil tindakan admin")

    def continue_job(self, job_id):
        job = self.jobs[job_id]
        if job.status != Status.attention or job.controller:
            raise ValueError("Tutup kontrol browser sebelum melanjutkan")
        job.resume.set()

    async def cancel(self, job_id):
        job = self.jobs[job_id]
        if job.status in TERMINAL:
            return
        if job.task:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
            if job.status not in TERMINAL:
                self.update(job, Status.cancelled, "Dibatalkan sebelum browser dibuka")
        else:
            self.update(job, Status.cancelled, "Dibatalkan sebelum mulai")

    async def close(self):
        self.closing = True
        for job in list(self.jobs.values()):
            await self.cancel(job.id)
        self.wakeup.set()
        if self.runner:
            await self.runner
        self.secrets = None
