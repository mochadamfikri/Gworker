"""Environment configuration for the Family Link worker.

All tunables come from the environment (``FAMILYLINK_*``) with safe defaults so
missing config never silently breaks a Termux install. Nothing here is secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"FAMILYLINK_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(f"FAMILYLINK_{name}")
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(f"FAMILYLINK_{name}")
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass
class Config:
    """Runtime configuration, loaded from the environment."""

    home: Path
    max_family_members: int = 6
    max_pending_jobs: int = 5
    max_attempts: int = 5
    backoff_base_seconds: float = 2.0
    backoff_cap_seconds: float = 3600.0
    total_time_window_seconds: int = 86400
    official_base_url: str = "https://families.google.com"

    @property
    def db_path(self) -> Path:
        return self.home / "familylink.db"

    @property
    def key_path(self) -> Path:
        return self.home / "secret.key"

    @classmethod
    def load(cls) -> "Config":
        home = Path(_env("HOME", str(Path.home() / ".familylink"))).expanduser()
        return cls(
            home=home,
            max_family_members=_env_int("MAX_FAMILY_MEMBERS", 6),
            max_pending_jobs=_env_int("MAX_PENDING_JOBS", 5),
            max_attempts=_env_int("MAX_ATTEMPTS", 5),
            backoff_base_seconds=_env_float("BACKOFF_BASE_SECONDS", 2.0),
            backoff_cap_seconds=_env_float("BACKOFF_CAP_SECONDS", 3600.0),
            total_time_window_seconds=_env_int("TOTAL_TIME_WINDOW_SECONDS", 86400),
            official_base_url=_env("OFFICIAL_BASE_URL", "https://families.google.com"),
        )

    def ensure_home(self) -> None:
        """Create the private home directory with restrictive (0700) perms."""
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.home, 0o700)
        except OSError:
            # Some Android/Termux mounts don't support chmod; not fatal.
            pass
