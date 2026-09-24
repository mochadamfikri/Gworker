"""Persist public job metadata only. Never accept arbitrary payloads."""
import sqlite3
import json
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, email TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL, created TEXT NOT NULL,
            updated TEXT NOT NULL)""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        for name, definition in {
            "action": "TEXT NOT NULL DEFAULT 'purchase'",
            "source_id": "TEXT NOT NULL DEFAULT ''",
            "result": "TEXT NOT NULL DEFAULT ''",
            "session_status": "TEXT NOT NULL DEFAULT 'not_saved'",
            "account_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "amount_usd": "TEXT NOT NULL DEFAULT ''",
            "limit_usd": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        self.db.execute("""UPDATE jobs SET status='interrupted',
            phase='Server restart; periksa hasil terakhir sebelum membuat batch baru'
            WHERE status IN ('queued','running','need_attention')""")
        self.db.execute("CREATE TABLE IF NOT EXISTS address (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS batches (request_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL)")
        self.db.commit()

    def put(self, job, commit=True):
        self.db.execute("""INSERT OR REPLACE INTO jobs
            (id,batch_id,email,status,phase,created,updated,action,source_id,result,session_status,account_status,amount_usd,limit_usd)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            job.id, job.batch_id, job.email, job.status.value,
            job.phase, job.created, job.updated,
            job.action, job.source_id, job.result, job.session_status,
            job.account_status,
            job.amount_usd, job.limit_usd,
        ))
        if commit:
            self.db.commit()

    def record_payment_intent(self, request_id, job):
        with self.db:
            self.db.execute("INSERT INTO batches VALUES (?, ?)", (str(request_id),job.id))
            self.put(job, commit=False)

    def list(self):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM jobs ORDER BY created DESC LIMIT 1000")]

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def close(self):
        self.db.close()

    def save_address(self, address):
        self.db.execute("INSERT OR REPLACE INTO address VALUES (1, ?)",
                        (address.model_dump_json(),))
        self.db.commit()

    def get_address(self):
        row = self.db.execute("SELECT data FROM address WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def find_request(self, request_id):
        row = self.db.execute("SELECT batch_id FROM batches WHERE request_id=?", (str(request_id),)).fetchone()
        return row[0] if row else None

    def record_request(self, request_id, batch_id):
        self.db.execute("INSERT INTO batches VALUES (?, ?)", (str(request_id), batch_id))
        self.db.commit()
