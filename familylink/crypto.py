"""Encrypted-at-rest storage for the family-head identifier.

We never store passwords, cookies, or refresh tokens. Only the guardian's
account *identifier* (e.g. an email) is kept, and even that is encrypted with a
locally generated Fernet key stored at 0600.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet


class SecretBox:
    """Symmetric encryption backed by a local key file (perms 0600)."""

    def __init__(self, key_path: Path):
        self.key_path = key_path
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("FAMILYLINK_SECRET_KEY")
        if env_key:
            return env_key.encode("utf-8")
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
