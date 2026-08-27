"""src/ui/auth.py için deterministik testler (bkz. plan "Real login
(Gmail/Outlook) + per-user account ownership"). Route-seviyesi giriş/
çıkış davranışı (redirect, cookie) test_oauth_routes.py'de + aşağıdaki
küçük middleware testinde; burası yalnızca DB-backed session çekirdeği."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.providers.base import EmbeddingProvider, LLMProvider
from src.storage.db import get_connection
from src.ui.auth import (
    adopt_orphaned_data,
    create_session,
    create_user,
    destroy_session,
    get_current_user,
    get_user_by_email,
)


class _DummyLLMProvider(LLMProvider):
    def __init__(self, *args, **kwargs):
        pass

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}" if json_output else ""

    def is_available(self):
        return True


class _DummyEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *args, **kwargs):
        pass

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    @property
    def model_name(self):
        return "dummy-embedding"

    @property
    def dimension(self):
        return 8


@pytest.fixture
def anonymous_client(temp_db, monkeypatch):
    """BİLEREK giriş yapılmamış — auth_guard_middleware'in kendisini test
    etmek için (bkz. altta). Diğer tüm test dosyalarındaki `client`
    fixture'ı varsayılan olarak login_test_client ile giriş yapıyor."""
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    from src.ui.app import app

    with TestClient(app) as test_client:
        yield test_client


def _request(cookies: dict | None = None) -> Mock:
    req = Mock()
    req.cookies = cookies or {}
    return req


def test_create_user_then_get_user_by_email(temp_db):
    user = create_user("a@example.com")
    assert user["email"] == "a@example.com"
    assert get_user_by_email("a@example.com") == user


def test_create_user_idempotent(temp_db):
    first = create_user("a@example.com")
    second = create_user("a@example.com")
    assert first["id"] == second["id"]
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE email = ?", ("a@example.com",)).fetchone()[0]
    assert count == 1


def test_get_user_by_email_unknown_returns_none(temp_db):
    assert get_user_by_email("yok@example.com") is None


def test_create_session_and_get_current_user_roundtrip(temp_db):
    user = create_user("a@example.com")
    token = create_session(user["id"])
    assert get_current_user(_request({"session_token": token})) == user


def test_get_current_user_no_cookie_returns_none(temp_db):
    assert get_current_user(_request()) is None


def test_get_current_user_unknown_token_returns_none(temp_db):
    assert get_current_user(_request({"session_token": "bilinmeyen-token"})) is None


def test_get_current_user_expired_session_returns_none(temp_db):
    user = create_user("a@example.com")
    token = create_session(user["id"])
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (stale, token))
    assert get_current_user(_request({"session_token": token})) is None


def test_destroy_session_removes_row_and_invalidates(temp_db):
    user = create_user("a@example.com")
    token = create_session(user["id"])
    destroy_session(token)
    assert get_current_user(_request({"session_token": token})) is None
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE token = ?", (token,)).fetchone()[0] == 0


def test_adopt_orphaned_data_only_touches_unowned_rows(temp_db):
    other_user = create_user("digeri@example.com")
    user = create_user("a@example.com")

    with get_connection() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO accounts (id, provider, account_type, email, connected_at, status, user_id) "
            "VALUES ('orphan', 'google', 'personal', 'orphan@example.com', ?, 'active', NULL)",
            (now,),
        )
        conn.execute(
            "INSERT INTO accounts (id, provider, account_type, email, connected_at, status, user_id) "
            "VALUES ('owned', 'google', 'personal', 'owned@example.com', ?, 'active', ?)",
            (now, other_user["id"]),
        )

    adopt_orphaned_data(user["id"])

    with get_connection() as conn:
        orphan_owner = conn.execute("SELECT user_id FROM accounts WHERE id = 'orphan'").fetchone()["user_id"]
        already_owned = conn.execute("SELECT user_id FROM accounts WHERE id = 'owned'").fetchone()["user_id"]
    assert orphan_owner == user["id"]
    assert already_owned == other_user["id"]  # başka birine ait satıra DOKUNULMADI


# --- auth_guard_middleware (bkz. src/ui/app.py) ---


def test_unauthenticated_request_redirects_to_giris(anonymous_client):
    response = anonymous_client.get("/anasayfa", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/giris"


def test_giris_page_reachable_without_session(anonymous_client):
    response = anonymous_client.get("/giris")
    assert response.status_code == 200
    assert "Gmail" in response.text and "Outlook" in response.text


def test_authenticated_session_reaches_protected_page(anonymous_client):
    user = create_user("a@example.com")
    token = create_session(user["id"])
    anonymous_client.cookies.set("session_token", token)
    response = anonymous_client.get("/anasayfa")
    assert response.status_code == 200
