"""SQLite persistence: family heads, jobs, consent, state transitions, audit.

Uses only the stdlib ``sqlite3``. All timestamps are UTC ISO strings. Reads
return typed models via ``*_from_row`` so raw rows never leak out.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import List, Optional

from ..models import (
    AuditEvent,
    ConsentRecord,
    FamilyHead,
    Job,
    JobResult,
    JobState,
    Operation,
    StateTransition,
    iso,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS family_heads (
    id TEXT PRIMARY KEY,
    identifier_enc TEXT NOT NULL,
    identifier_fingerprint TEXT NOT NULL UNIQUE,
    label TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consent_records (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    guardian_name TEXT NOT NULL,
    relationship TEXT,
    consent_given INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    family_head_id TEXT NOT NULL,
    child_display_name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    result TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_attempt_at TEXT,
    deadline_at TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _new_id() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._harden_perms()

    def _harden_perms(self) -> None:
        for path in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            if path.exists():
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- family heads -------------------------------------------------
    def add_family_head(self, identifier_enc: str, fingerprint: str, label: Optional[str]) -> FamilyHead:
        now = iso(utcnow())
        existing = self.get_family_head_by_fingerprint(fingerprint)
        if existing:
            return existing
        fid = _new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO family_heads (id, identifier_enc, identifier_fingerprint, label, created_at)"
                " VALUES (?,?,?,?,?)",
                (fid, identifier_enc, fingerprint, label, now),
            )
            self._conn.commit()
        return FamilyHead(id=fid, identifier_fingerprint=fingerprint, label=label, created_at=now)

    def get_family_head(self, fid: str) -> Optional[FamilyHead]:
        row = self._conn.execute("SELECT * FROM family_heads WHERE id=?", (fid,)).fetchone()
        return _family_head_from_row(row) if row else None

    def get_family_head_by_fingerprint(self, fingerprint: str) -> Optional[FamilyHead]:
        row = self._conn.execute(
            "SELECT * FROM family_heads WHERE identifier_fingerprint=?", (fingerprint,)
        ).fetchone()
        return _family_head_from_row(row) if row else None

    def get_family_head_identifier_enc(self, fid: str) -> Optional[str]:
        row = self._conn.execute("SELECT identifier_enc FROM family_heads WHERE id=?", (fid,)).fetchone()
        return row["identifier_enc"] if row else None

    def list_family_heads(self) -> List[FamilyHead]:
        rows = self._conn.execute("SELECT * FROM family_heads ORDER BY created_at").fetchall()
        return [_family_head_from_row(r) for r in rows]

    # ---- jobs ---------------------------------------------------------
    def get_job(self, job_id: str) -> Optional[Job]:
        row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job_from_row(row) if row else None

    def get_job_by_idempotency_key(self, key: str) -> Optional[Job]:
        row = self._conn.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
        return _job_from_row(row) if row else None

    def insert_job(self, job: Job) -> Job:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, idempotency_key, family_head_id, child_display_name, birth_date,"
                " operation, state, result, attempts, max_attempts, next_attempt_at, deadline_at, note,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job.id,
                    job.idempotency_key,
                    job.family_head_id,
                    job.child_display_name,
                    job.birth_date,
                    job.operation.value,
                    job.state.value,
                    job.result.value if job.result else None,
                    job.attempts,
                    job.max_attempts,
                    job.next_attempt_at,
                    job.deadline_at,
                    job.note,
                    job.created_at,
                    job.updated_at,
                ),
            )
            self._conn.commit()
        return job

    def update_job(self, job: Job) -> Job:
        job.updated_at = iso(utcnow())
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET state=?, result=?, attempts=?, max_attempts=?, next_attempt_at=?,"
                " deadline_at=?, note=?, updated_at=? WHERE id=?",
                (
                    job.state.value,
                    job.result.value if job.result else None,
                    job.attempts,
                    job.max_attempts,
                    job.next_attempt_at,
                    job.deadline_at,
                    job.note,
                    job.updated_at,
                    job.id,
                ),
            )
            self._conn.commit()
        return job

    def list_jobs(self, family_head_id: Optional[str] = None) -> List[Job]:
        if family_head_id:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE family_head_id=? ORDER BY created_at DESC", (family_head_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [_job_from_row(r) for r in rows]

    def count_jobs_by_result(self, family_head_id: str, results: List[JobResult]) -> int:
        placeholders = ",".join("?" for _ in results)
        params = [family_head_id] + [r.value for r in results]
        row = self._conn.execute(
            f"SELECT COUNT(*) AS c FROM jobs WHERE family_head_id=? AND result IN ({placeholders})",
            params,
        ).fetchone()
        return int(row["c"])

    def count_jobs_by_state(self, family_head_id: str, states: List[JobState]) -> int:
        placeholders = ",".join("?" for _ in states)
        params = [family_head_id] + [s.value for s in states]
        row = self._conn.execute(
            f"SELECT COUNT(*) AS c FROM jobs WHERE family_head_id=? AND state IN ({placeholders})",
            params,
        ).fetchone()
        return int(row["c"])

    # ---- consent ------------------------------------------------------
    def add_consent(
        self, job_id: str, guardian_name: str, relationship: Optional[str], consent_given: bool
    ) -> ConsentRecord:
        now = iso(utcnow())
        cid = _new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO consent_records (id, job_id, guardian_name, relationship, consent_given, confirmed_at)"
                " VALUES (?,?,?,?,?,?)",
                (cid, job_id, guardian_name, relationship, 1 if consent_given else 0, now),
            )
            self._conn.commit()
        return ConsentRecord(
            id=cid,
            job_id=job_id,
            guardian_name=guardian_name,
            relationship=relationship,
            consent_given=consent_given,
            confirmed_at=now,
        )

    def get_consent(self, job_id: str) -> Optional[ConsentRecord]:
        row = self._conn.execute(
            "SELECT * FROM consent_records WHERE job_id=? ORDER BY confirmed_at DESC LIMIT 1", (job_id,)
        ).fetchone()
        return _consent_from_row(row) if row else None

    # ---- transitions & audit -----------------------------------------
    def add_transition(
        self, job_id: str, from_state: Optional[str], to_state: str, note: Optional[str] = None
    ) -> StateTransition:
        now = iso(utcnow())
        tid = _new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO state_transitions (id, job_id, from_state, to_state, note, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (tid, job_id, from_state, to_state, note, now),
            )
            self._conn.commit()
        return StateTransition(
            id=tid, job_id=job_id, from_state=from_state, to_state=to_state, note=note, created_at=now
        )

    def list_transitions(self, job_id: str) -> List[StateTransition]:
        rows = self._conn.execute(
            "SELECT * FROM state_transitions WHERE job_id=? ORDER BY created_at", (job_id,)
        ).fetchall()
        return [_transition_from_row(r) for r in rows]

    def add_audit(self, event_type: str, message: str, job_id: Optional[str] = None) -> AuditEvent:
        now = iso(utcnow())
        aid = _new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events (id, job_id, event_type, message, created_at) VALUES (?,?,?,?,?)",
                (aid, job_id, event_type, message, now),
            )
            self._conn.commit()
        return AuditEvent(id=aid, job_id=job_id, event_type=event_type, message=message, created_at=now)

    def list_audit(self, job_id: Optional[str] = None) -> List[AuditEvent]:
        if job_id:
            rows = self._conn.execute(
                "SELECT * FROM audit_events WHERE job_id=? ORDER BY created_at", (job_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM audit_events ORDER BY created_at").fetchall()
        return [_audit_from_row(r) for r in rows]


# --- row -> model helpers (never leak raw rows) ------------------------
def _family_head_from_row(row: sqlite3.Row) -> FamilyHead:
    return FamilyHead(
        id=row["id"],
        identifier_fingerprint=row["identifier_fingerprint"],
        label=row["label"],
        created_at=row["created_at"],
    )


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        idempotency_key=row["idempotency_key"],
        family_head_id=row["family_head_id"],
        child_display_name=row["child_display_name"],
        birth_date=row["birth_date"],
        operation=Operation(row["operation"]),
        state=JobState(row["state"]),
        result=JobResult(row["result"]) if row["result"] else None,
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        next_attempt_at=row["next_attempt_at"],
        deadline_at=row["deadline_at"],
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _consent_from_row(row: sqlite3.Row) -> ConsentRecord:
    return ConsentRecord(
        id=row["id"],
        job_id=row["job_id"],
        guardian_name=row["guardian_name"],
        relationship=row["relationship"],
        consent_given=bool(row["consent_given"]),
        confirmed_at=row["confirmed_at"],
    )


def _transition_from_row(row: sqlite3.Row) -> StateTransition:
    return StateTransition(
        id=row["id"],
        job_id=row["job_id"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        note=row["note"],
        created_at=row["created_at"],
    )


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        job_id=row["job_id"],
        event_type=row["event_type"],
        message=row["message"],
        created_at=row["created_at"],
    )
