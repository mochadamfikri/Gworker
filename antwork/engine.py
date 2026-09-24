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
    action: str = "purchase"
    source_id: str = ""
    result: str = ""
    session_status: str = "not_saved"
    account_status: str = "unknown"
    resume: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    browser: object = field(default=None, repr=False)
    task: object = field(default=None, repr=False)
    controller: bool = False


class Engine:
    def __init__(self, store, driver_factory, max_concurrency=5, attention_timeout=900, vault=None):
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
        self.vault = vault

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
        for account in payload.accounts:
            job = Job(str(uuid4()), self.batch_id, account.email)
            self.jobs[job.id] = job
            self.store.put(job)
        self.ensure_runner()
        return self.batch_id

    def ensure_runner(self):
        if self.runner is None or self.runner.done():
            self.runner = asyncio.create_task(self._schedule())
        self.wakeup.set()

    def enqueue_action(self, source_id, action):
        if action not in {"open_session", "check_email", "retry_kyc"}:
            raise ValueError("Aksi tidak dikenal")
        source = self.store.get(source_id)
        if not source or source["action"] != "purchase":
            raise ValueError("Worker asal tidak ditemukan")
        if Status(source["status"]) not in TERMINAL:
            raise ValueError("Tunggu worker asal selesai")
        if any(j.source_id == source_id and (j.status not in TERMINAL or (j.task and not j.task.done()))
               for j in self.jobs.values()):
            raise ValueError("Sesi akun ini sedang dipakai")
        source_job = self.jobs.get(source_id)
        if source_job and source_job.task and not source_job.task.done():
            raise ValueError("Sesi akun masih disimpan; coba lagi sebentar")
        if not self.vault or not self.vault.exists(source_id):
            raise ValueError("Belum ada sesi login tersimpan")
        job = Job(str(uuid4()), source["batch_id"], source["email"], action=action,
                  source_id=source_id, session_status="saved")
        self.jobs[job.id] = job
        self.store.put(job)
        # A cancelled batch may have set concurrency=0; use at least one slot.
        self.concurrency = max(1, self.concurrency)
        self.ensure_runner()
        return job.id

    async def _schedule(self):
        try:
            while not self.closing:
                self.wakeup.clear()
                if self.secrets and all(j.status in TERMINAL and (j.task is None or j.task.done())
                    for j in self.jobs.values() if j.action == "purchase" and j.batch_id == self.batch_id):
                    self.secrets = None
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
        checkpoint = None
        try:
            driver = self.driver_factory()
            job.browser = driver
            if self.vault:
                checkpoint = asyncio.create_task(self.periodic_checkpoint(job))
            if job.action == "purchase":
                account = next(a for a in self.secrets.accounts if a.email == job.email)
                await driver.run(job, account, self.secrets, self.attention)
                self.update(job, Status.completed, "Pembelian saldo terkonfirmasi")
            else:
                saved = self.vault.load(job.source_id, job.email)
                if not saved:
                    raise ValueError("Saved session unavailable")
                outcome = await driver.maintain(job, saved, self.attention)
                messages = {
                    "session_opened": "Sesi selesai dibuka",
                    "suspended": "Email menyatakan suspend; akun ditandai gagal",
                    "no_suspend_detected": "Belum terdeteksi email suspend; bukan konfirmasi akun lolos",
                    "verification_required": "Email meminta verifikasi identitas",
                    "needs_review": "Hasil email perlu diperiksa manual",
                    "resend_confirmed": "Pengiriman ulang link verifikasi terkonfirmasi",
                }
                if outcome not in messages:
                    raise ValueError("Unverified action result")
                job.result = outcome
                source = self.store.get(job.source_id)
                if source and outcome in {"suspended", "no_suspend_detected", "verification_required", "needs_review"}:
                    source["status"] = Status(source["status"])
                    # A later empty inbox must not erase an established suspension.
                    if source["account_status"] != "suspended":
                        source["account_status"] = outcome
                    self.store.put(Job(**source))
                self.update(job, Status.failed if outcome == "suspended" else Status.completed, messages[outcome])
        except asyncio.CancelledError:
            self.update(job, Status.cancelled, "Dibatalkan; cek transaksi terakhir sebelum mengulang")
        except TimeoutError:
            self.update(job, Status.failed, "Waktu verifikasi habis; tidak dicoba ulang otomatis")
        except Exception:
            # Playwright exceptions can contain passwords, URLs, or DOM text.
            self.update(job, Status.failed, "Proses gagal; periksa akun sebelum mencoba lagi")
        finally:
            if checkpoint:
                checkpoint.cancel()
                await asyncio.gather(checkpoint, return_exceptions=True)
            if driver:
                await self.save_session(job)
                try:
                    await driver.close()
                except Exception:
                    pass
            job.browser = None
            job.controller = False
            # Wake after this task has actually become done.
            asyncio.get_running_loop().call_soon(self.wakeup.set)

    async def periodic_checkpoint(self, job):
        while True:
            await asyncio.sleep(30)
            await self.save_session(job)

    async def attention(self, job, message):
        await self.save_session(job)
        job.resume.clear()
        self.update(job, Status.attention, message)
        await asyncio.wait_for(job.resume.wait(), self.attention_timeout)
        self.update(job, Status.running, "Memeriksa hasil tindakan admin")

    async def save_session(self, job):
        if not self.vault or not job.browser or not getattr(job.browser, "context", None):
            return
        try:
            state = await job.browser.context.storage_state()
            saved = self.vault.save(job.source_id or job.id, job.email, state)
            if saved:
                job.session_status = "saved"
        except Exception:
            job.session_status = "save_failed"
        self.store.put(job)
        if job.source_id:
            source = self.store.get(job.source_id)
            if source:
                source["status"] = Status(source["status"])
                source["session_status"] = job.session_status
                self.store.put(Job(**source))

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
