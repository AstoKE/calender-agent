"""localization_preferences tablosu için testler — FK'lı ve önceden hiç
kullanılmamış bir tablo olduğundan davranışı ayrı doğrulanıyor."""

from __future__ import annotations

from src.connectors.account_registry import ensure_account_registered
from src.localization.preferences import (
    get_effective_timezone,
    get_localization_preference,
    set_timezone,
    set_ui_language,
)
from src.services.timeutil import DEFAULT_TIMEZONE


def test_set_ui_language_for_unknown_account_is_silent_noop(temp_db):
    set_ui_language("hic-kayitli-degil", "en")
    assert get_localization_preference("hic-kayitli-degil") is None


def test_set_and_get_ui_language(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    set_ui_language("acc1", "en")
    pref = get_localization_preference("acc1")
    assert pref["ui_language"] == "en"


def test_set_ui_language_upserts(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    set_ui_language("acc1", "en")
    set_ui_language("acc1", "tr")
    pref = get_localization_preference("acc1")
    assert pref["ui_language"] == "tr"


def test_set_timezone_does_not_clobber_language(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    set_ui_language("acc1", "en")
    set_timezone("acc1", "Europe/London")
    pref = get_localization_preference("acc1")
    assert pref["ui_language"] == "en"
    assert pref["timezone"] == "Europe/London"


def test_get_effective_timezone_defaults(temp_db):
    assert get_effective_timezone(None) == DEFAULT_TIMEZONE
    assert get_effective_timezone("no-such-account") == DEFAULT_TIMEZONE


def test_get_effective_timezone_uses_preference(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    set_timezone("acc1", "Europe/London")
    assert get_effective_timezone("acc1") == "Europe/London"
