"""src/connectors/account_registry.py için testler — özellikle
resolve_write_account_id (bkz. Ayarlar ekranı "Ana takvim hesabı")."""

from __future__ import annotations

from src.connectors.account_registry import (
    MASTER_CALENDAR_PREFERENCE_KEY,
    ensure_account_registered,
    get_account,
    resolve_write_account_id,
)
from src.storage.preferences import set_preference
from src.ui.auth import create_user


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


def test_ensure_account_registered_reassigns_ownership_on_reconnect(temp_db):
    # Canlı testte bulunan gerçek hata: bir hesap zaten BAŞKA bir kullanıcıya
    # aitken, o hesabı gerçek bir OAuth onayından geçerek yeniden bağlayan
    # kişi sessizce hiçbir sonuç göremiyordu — sahiplik hiç değişmiyordu.
    user_a = create_user("a@users.example.com")
    user_b = create_user("b@users.example.com")

    ensure_account_registered("acc1", provider="google", email="a@example.com", user_id=user_a["id"])
    assert get_account("acc1")["user_id"] == user_a["id"]

    ensure_account_registered("acc1", provider="google", email="a@example.com", user_id=user_b["id"])
    assert get_account("acc1")["user_id"] == user_b["id"]


def test_ensure_account_registered_no_user_id_does_not_clear_existing_owner(temp_db):
    # CLI (user_id=None, bkz. select_account()) mevcut bir hesabın web'den
    # kazanılmış sahipliğini YANLIŞLIKLA silmemeli.
    user_a = create_user("a@users.example.com")
    ensure_account_registered("acc1", provider="google", email="a@example.com", user_id=user_a["id"])
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    assert get_account("acc1")["user_id"] == user_a["id"]
