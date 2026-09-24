"""Encrypted, per-worker authentication cookies; never browser form contents.

Only Google, Claude and Persona authentication-domain cookies are retained.
Bank/payment-provider state, localStorage, IndexedDB, email bodies and autofill
are not persisted. The current allowed-provider URL is encrypted for Retry.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID
from urllib.parse import urlparse

from cryptography.fernet import Fernet


AUTH_DOMAINS = {"google.com", "accounts.google.com", "mail.google.com",
                "claude.com", "platform.claude.com", "anthropic.com", "console.anthropic.com",
                "withpersona.com", "inquiry.withpersona.com", "perso.na"}


def safe_resume_url(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None,443):
            return None
        host = parsed.hostname or ""
        if host in AUTH_DOMAINS or host.endswith(".withpersona.com"):
            return url
    except ValueError:
        pass
    return None


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

    def save(self, worker_id, email, state, resume_url=None):
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
            "resume_url": safe_resume_url(resume_url),
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
        data = self._read(worker_id, email)
        return data["state"] if data else None

    def resume_url(self, worker_id, email):
        data = self._read(worker_id, email)
        return safe_resume_url(data.get("resume_url")) if data else None

    def _read(self, worker_id, email):
        path = self.path(worker_id)
        if not path.exists():
            return None
        data = json.loads(self.cipher.decrypt(path.read_bytes()))
        if data.get("version") != 1 or data.get("worker_id") != str(UUID(str(worker_id))) or data.get("account") != self.binding(email):
            raise ValueError("Saved session does not belong to this worker")
        return data

    def exists(self, worker_id):
        return self.path(worker_id).exists()
