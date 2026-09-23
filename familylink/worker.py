"""Bounded worker orchestration for Family Link jobs.

Google login, parent verification, OTP entry, CAPTCHA and identity/security
challenges remain human-controlled. The worker stops and requests a human
when such a challenge is reported.
"""
from __future__ import annotations
import threading
from dataclasses import dataclass
from typing import Any
from .models import JobResult, JobState
from .pvapins import PVAPinsClient, PVAPinsError
from .service import FamilyLinkService
from .workflow import build_handoff

@dataclass
class WorkerSnapshot:
    running: bool = False
    last_error: str | None = None
    active_job: str | None = None
    phase: str = "idle"
    provider_order: str | None = None
    phone: str | None = None
    otp_received: bool = False
    challenge_required: bool = False

class WorkerController:
    def __init__(self, service: FamilyLinkService, pvapins: PVAPinsClient | None):
        self.service = service
        self.pvapins = pvapins
        self.snapshot = WorkerSnapshot()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._handled_jobs: set[str] = set()

    def start(self) -> bool:
        with self._lock:
            if self.snapshot.running:
                return False
            self._stop.clear()
            self.snapshot.running = True
            self.snapshot.last_error = None
            self.snapshot.phase = "scanning_jobs"
            self._thread = threading.Thread(target=self._run, daemon=True, name="gworker")
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self.snapshot.running = False
            self.snapshot.phase = "stopped"

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self.snapshot.__dict__.copy()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                jobs = self.service.store.list_jobs()
                pending = [j for j in jobs if j.state in {JobState.AWAITING_HUMAN_ACTION, JobState.IN_REVIEW} and j.id not in self._handled_jobs]
                if not pending:
                    self.snapshot.phase = "idle"
                    self._stop.wait(2.0)
                    continue
                job = pending[0]
                self._handle_job(job)
                self._stop.wait(1.0)
        except Exception as exc:
            with self._lock:
                self.snapshot.last_error = str(exc)
                self.snapshot.phase = "error"
                self.snapshot.running = False

    def _handle_job(self, job) -> None:
        self.snapshot.active_job = job.id
        self.snapshot.phase = "official_handoff"
        handoff = build_handoff(self.service.config, job.operation)
        self.service.store.add_audit("worker.handoff", f"Official handoff prepared: {handoff.url}", job.id)
        self.snapshot.phase = "waiting_human"
        self._handled_jobs.add(job.id)

    def reserve_phone(self, country: str | None = None, service: str | None = None,
                      operator: int | None = None) -> dict[str, Any]:
        if not self.pvapins:
            raise PVAPinsError("PVAPins is not configured.")
        self.snapshot.phase = "reserving_number"
        data = self.pvapins.reserve(country, service, operator)
        self.snapshot.provider_order = str(data.get("id") or data.get("orderId") or "")
        self.snapshot.phone = data.get("phoneNumber") or data.get("phone")
        self.snapshot.phase = "number_ready"
        return data

    def poll_provider_otp(self, order_id: str, timeout: int = 120) -> dict[str, Any]:
        if not self.pvapins:
            raise PVAPinsError("PVAPins is not configured.")
        self.snapshot.phase = "waiting_provider_otp"
        data = self.pvapins.wait_for_otp(order_id, timeout=timeout)
        self.snapshot.otp_received = bool(data.get("otpCode") or data.get("otp"))
        self.snapshot.phase = "otp_received" if self.snapshot.otp_received else "provider_done"
        return data

    def mark_google_challenge(self, job_id: str, note: str = "") -> None:
        self.snapshot.challenge_required = True
        self.snapshot.phase = "human_required"
        note = note.strip() or "Google security challenge/CAPTCHA requires human action."
        self.service.store.add_audit("worker.challenge", note, job_id)
        job = self.service.store.get_job(job_id)
        if job and job.state not in {JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED}:
            self.service.record_result(job_id, JobResult.PENDING_HUMAN_ACTION)

    def clear_challenge(self) -> None:
        self.snapshot.challenge_required = False
        if self.snapshot.running:
            self.snapshot.phase = "waiting_human"
