"""src/connectors/account_registry.py için testler — özellikle
resolve_write_account_id (bkz. Ayarlar ekranı "Ana takvim hesabı")."""

from __future__ import annotations

from src.connectors.account_registry import (
    MASTER_CALENDAR_PREFERENCE_KEY,
    ensure_account_registered,
    resolve_write_account_id,
)
from src.storage.preferences import set_preference


def test_resolve_write_account_id_no_master_returns_fallback(temp_db):
    assert resolve_write_account_id("acc1") == "acc1"


def test_resolve_write_account_id_uses_master_when_set_and_valid(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    set_preference(MASTER_CALENDAR_PREFERENCE_KEY, "acc2")

    assert resolve_write_account_id("acc1") == "acc2"


def test_resolve_write_account_id_falls_back_when_master_no_longer_registered(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    set_preference(MASTER_CALENDAR_PREFERENCE_KEY, "deleted-account")

    assert resolve_write_account_id("acc1") == "acc1"


def test_resolve_write_account_id_master_equal_to_fallback_is_a_noop(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    set_preference(MASTER_CALENDAR_PREFERENCE_KEY, "acc1")

    assert resolve_write_account_id("acc1") == "acc1"
