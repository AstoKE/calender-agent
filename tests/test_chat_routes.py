"""`POST /asistan/mesaj` / `POST /asistan/sifirla` için ince TestClient
testleri (bkz. plan "Web Chatbox" Faz 5). Yapıya bakar (cookie, redirect
hedefi, DB satırı) — gerçek LLM/Google Calendar hiç çağrılmaz: `_DummyLLMProvider`
her zaman "{}" döner, `classify_intent` bunu doğrulayamayıp OTHER'a düşer
(bkz. src/services/intent.py) — yani bu testler hiçbir zaman gerçek bir
create_event/query_calendar akışını tetiklemez, yalnızca oturum/cookie/DB
iskeletini doğrular. `_get_calendar` de sahte bir bağlayıcıyla değiştiriliyor
(gerçek Google OAuth tetiklenmesin diye — bkz. src/ui/routes.py::_get_calendar
'interaktif-OAuth-toleranslı')."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.connectors.account_registry import ensure_account_registered
from src.providers.base import EmbeddingProvider, LLMProvider
from src.storage.db import get_connection


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


class _NullCalendar:
    def list_events(self, *args, **kwargs):
        return []


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.chat_routes._get_calendar", lambda request, account_id: _NullCalendar())
    from src.ui.app import app

    with TestClient(app) as test_client:
        yield test_client


def test_message_sets_cookie_and_reuses_it_on_second_message(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = client.post("/asistan/mesaj", data={"metin": "merhaba"})
    assert response.status_code == 200  # TestClient takip ediyor -> son yanıt /anasayfa'nın 200'ü
    session_cookie = client.cookies.get("chat_session")
    assert session_cookie

    response2 = client.post("/asistan/mesaj", data={"metin": "tekrar merhaba"})
    assert response2.status_code == 200
    assert client.cookies.get("chat_session") == session_cookie

    with get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert rows == 1  # tek satır, iki mesaj aynı oturuma yazıldı


def test_message_redirects_to_next_target(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = client.post(
        "/asistan/mesaj", data={"metin": "merhaba", "next": "/anasayfa?foo=1"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa?foo=1"


def test_message_with_no_active_account_creates_no_session(client):
    response = client.post("/asistan/mesaj", data={"metin": "merhaba"}, follow_redirects=False)
    assert response.status_code == 303
    assert "chat_session" not in response.cookies

    with get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert rows == 0


def test_empty_message_is_ignored(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post("/asistan/mesaj", data={"metin": "   "}, follow_redirects=False)
    assert response.status_code == 303

    with get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert rows == 0


def test_reset_clears_flow_and_step_but_keeps_session(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "merhaba"})
    session_id = client.cookies.get("chat_session")

    with get_connection() as conn:
        conn.execute(
            "UPDATE chat_sessions SET flow = 'create_event', step = 'ask_title' WHERE session_id = ?",
            (session_id,),
        )

    response = client.post("/asistan/sifirla", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa"

    with get_connection() as conn:
        row = conn.execute(
            "SELECT session_id, flow, step FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    assert row["session_id"] == session_id  # oturum korunuyor
    assert row["flow"] is None
    assert row["step"] is None


def test_reset_with_no_active_account_does_not_error(client):
    response = client.post("/asistan/sifirla", follow_redirects=False)
    assert response.status_code == 303


def test_message_history_survives_simulated_restart(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "merhaba, ilk mesaj"})
    session_id = client.cookies.get("chat_session")

    # "Sunucu yeniden başlar" -> aynı (monkeypatch'lenmiş) DB dosyasına karşı
    # taze bir app/TestClient (bkz. plan Faz 5 doğrulama maddesi).
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.chat_routes._get_calendar", lambda request, account_id: _NullCalendar())
    from src.ui.app import app as restarted_app

    with TestClient(restarted_app) as restarted_client:
        page = restarted_client.get(
            "/anasayfa", cookies={"chat_session": session_id, "active_account": "acc1"}
        )
        assert page.status_code == 200
        assert "merhaba, ilk mesaj" in page.text


# --- AJAX fragment yanıtı (bkz. templates/anasayfa.html'deki gönderim script'i) ---


def test_ajax_message_returns_fragment_not_redirect(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = client.post(
        "/asistan/mesaj",
        data={"metin": "merhaba"},
        headers={"X-Requested-With": "fetch"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="asistan-chat"' in response.text
    assert "merhaba" in response.text
    assert client.cookies.get("chat_session")


def test_ajax_reset_returns_fragment_not_redirect(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "merhaba"})

    response = client.post(
        "/asistan/sifirla", headers={"X-Requested-With": "fetch"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert 'id="asistan-chat"' in response.text


def test_ajax_empty_message_returns_unchanged_fragment_without_new_session(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = client.post(
        "/asistan/mesaj", data={"metin": "   "}, headers={"X-Requested-With": "fetch"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert 'id="asistan-chat"' in response.text
    assert "chat_session" not in response.cookies

    with get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert rows == 0


def test_non_ajax_message_still_redirects(client):
    """JS kapalıysa/başarısızsa form normal şekilde gönderilir — AJAX
    desteği eski davranışı değiştirmemeli (bkz. plan)."""
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post("/asistan/mesaj", data={"metin": "merhaba"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa"
