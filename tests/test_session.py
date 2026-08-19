"""src/ui/session.py için deterministik testler — sıfır hesap / bayat cookie
/ open-redirect koruması invariant'ları burada sabitleniyor (bkz. plan)."""

from __future__ import annotations

from unittest.mock import Mock

from src.ui.session import resolve_active_account, resolve_language, safe_next


def _request(cookies: dict | None = None, headers: dict | None = None) -> Mock:
    req = Mock()
    req.cookies = cookies or {}
    req.headers = headers or {}
    return req


# --- resolve_active_account ---


def test_resolve_active_account_zero_accounts_returns_none():
    assert resolve_active_account(_request(), accounts=[]) is None


def test_resolve_active_account_no_cookie_returns_first():
    accounts = [{"id": "a1"}, {"id": "a2"}]
    assert resolve_active_account(_request(), accounts=accounts) == {"id": "a1"}


def test_resolve_active_account_valid_cookie():
    accounts = [{"id": "a1"}, {"id": "a2"}]
    req = _request(cookies={"active_account": "a2"})
    assert resolve_active_account(req, accounts=accounts) == {"id": "a2"}


def test_resolve_active_account_stale_cookie_falls_back_to_first():
    accounts = [{"id": "a1"}, {"id": "a2"}]
    req = _request(cookies={"active_account": "silinmis-hesap"})
    assert resolve_active_account(req, accounts=accounts) == {"id": "a1"}


# --- resolve_language ---


def test_resolve_language_cookie_wins():
    req = _request(cookies={"ui_lang": "en"})
    assert resolve_language(req) == "en"


def test_resolve_language_accept_language_fallback():
    req = _request(headers={"accept-language": "en-US,en;q=0.9"})
    assert resolve_language(req) == "en"


def test_resolve_language_defaults_to_tr():
    assert resolve_language(_request()) == "tr"


def test_resolve_language_unsupported_accept_language_falls_back_to_default():
    req = _request(headers={"accept-language": "fr-FR,fr;q=0.9"})
    assert resolve_language(req) == "tr"


# --- safe_next ---


def test_safe_next_valid_path():
    assert safe_next("/oneriler") == "/oneriler"


def test_safe_next_none_falls_back():
    assert safe_next(None) == "/anasayfa"


def test_safe_next_missing_leading_slash_falls_back():
    assert safe_next("oneriler") == "/anasayfa"


def test_safe_next_protocol_relative_rejected():
    assert safe_next("//evil.example.com") == "/anasayfa"


def test_safe_next_backslash_rejected():
    assert safe_next("/\\evil.example.com") == "/anasayfa"


def test_safe_next_custom_fallback():
    assert safe_next(None, fallback="/hesaplar") == "/hesaplar"
