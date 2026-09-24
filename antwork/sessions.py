"""Encrypted, per-worker authentication cookies; never browser form contents.

Only Google and Claude authentication-domain cookies are retained. Payment,
Persona, localStorage, IndexedDB, email bodies and autofill are not persisted.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID

from cryptography.fernet import Fernet


AUTH_DOMAINS = {"google.com", "accounts.google.com", "mail.google.com",
                "claude.com", "platform.claude.com", "anthropic.com", "console.anthropic.com"}


class SessionVault:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        key_path = self.directory / "master.key"
        if not key_path.exists():
            if any(self.directory.glob("*.session")):
                raise RuntimeError("Session key is missing; restore the original key")
            try:
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
        key_path.chmod(0o600)
        self.cipher = Fernet(key_path.read_bytes())

    def path(self, worker_id):
        return self.directory / f"{UUID(str(worker_id))}.session"

    @staticmethod
    def binding(email):
        return hashlib.sha256(email.strip().lower().encode()).hexdigest()

    def save(self, worker_id, email, state):
        # Explicit allowlist: do not serialize arbitrary browser state.
        cookies = []
        for cookie in state.get("cookies", []):
            if cookie.get("domain", "").lstrip(".").lower() in AUTH_DOMAINS:
                cookies.append({k: cookie[k] for k in (
                    "name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite", "partitionKey"
                ) if k in cookie})
        if not cookies:
            return False
        data = self.cipher.encrypt(json.dumps({
            "version": 1, "worker_id": str(UUID(str(worker_id))), "account": self.binding(email),
            "state": {"cookies": cookies, "origins": []},
        }, separators=(",", ":")).encode())
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path(worker_id))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True

    def load(self, worker_id, email):
        path = self.path(worker_id)
        if not path.exists():
            return None
        data = json.loads(self.cipher.decrypt(path.read_bytes()))
        if data.get("version") != 1 or data.get("worker_id") != str(UUID(str(worker_id))) or data.get("account") != self.binding(email):
            raise ValueError("Saved session does not belong to this worker")
        return data["state"]

    def exists(self, worker_id):
        return self.path(worker_id).exists()
