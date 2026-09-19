"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from familylink.config import Config
from familylink.crypto import SecretBox
from familylink.models import fingerprint
from familylink.service import FamilyLinkService
from familylink.storage.store import Store


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return Config(
        home=tmp_path,
        max_family_members=3,
        max_pending_jobs=2,
        max_attempts=3,
        backoff_base_seconds=1.0,
        backoff_cap_seconds=10.0,
        total_time_window_seconds=3600,
    )


@pytest.fixture()
def store(config: Config) -> Store:
    s = Store(config.db_path)
    yield s
    s.close()


@pytest.fixture()
def service(store: Store, config: Config) -> FamilyLinkService:
    return FamilyLinkService(store, config)


@pytest.fixture()
def family_head(store: Store, config: Config):
    box = SecretBox(config.key_path)
    ident = "guardian@example.com"
    return store.add_family_head(box.encrypt(ident), fingerprint(ident), "Test Household")
