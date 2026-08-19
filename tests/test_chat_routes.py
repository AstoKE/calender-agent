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


def test_new_chat_opens_fresh_session_and_keeps_old_one_intact(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "merhaba"})
    old_session_id = client.cookies.get("chat_session")

    with get_connection() as conn:
        conn.execute(
            "UPDATE chat_sessions SET flow = 'create_event', step = 'ask_title' WHERE session_id = ?",
            (old_session_id,),
        )

    response = client.post("/asistan/yeni-sohbet", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa"

    new_session_id = client.cookies.get("chat_session")
    assert new_session_id != old_session_id  # yeni bir oturuma geçildi, eskisi sıfırlanmadı

    with get_connection() as conn:
        old_row = conn.execute(
            "SELECT flow, step FROM chat_sessions WHERE session_id = ?", (old_session_id,)
        ).fetchone()
        message_count = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (old_session_id,)
        ).fetchone()[0]
    assert old_row["flow"] == "create_event"  # eski oturum DOKUNULMADI
    assert old_row["step"] == "ask_title"
    assert message_count == 2  # eski mesajlar (kullanıcı + asistan) hâlâ duruyor


def test_new_chat_with_no_active_account_does_not_error(client):
    response = client.post("/asistan/yeni-sohbet", follow_redirects=False)
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


def test_ajax_new_chat_returns_fragment_not_redirect(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "merhaba"})
    old_session_id = client.cookies.get("chat_session")

    response = client.post(
        "/asistan/yeni-sohbet", headers={"X-Requested-With": "fetch"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert 'id="asistan-chat"' in response.text
    assert "merhaba" not in response.text  # yeni/boş oturum, eski mesaj görünmüyor
    assert client.cookies.get("chat_session") != old_session_id


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


def test_action_field_alone_is_processed_as_the_message(client):
    """Onayla/Düzenle/Reddet gibi hızlı-yanıt butonlarının sunucu tarafı
    sözleşmesi: `metin` boş, yalnızca `action` doluyken de mesaj işlenmeli.
    Canlı testte bulunan bir bug'ın (anasayfa.html'deki gönderim script'i
    tıklanan butonun name/value'sunu formData'ya hiç eklemiyordu, butonlar
    hiçbir şey yapmıyormuş gibi görünüyordu) sunucu tarafındaki varsayımını
    doğruluyor — JS'in kendisi burada test edilemiyor, yalnızca bu sözleşme."""
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post(
        "/asistan/mesaj", data={"action": "approve"}, headers={"X-Requested-With": "fetch"}
    )
    assert response.status_code == 200
    with get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert rows == 1  # action doluydu -> boş gönderim sayılmadı, oturum açıldı


# --- Geçmiş Sohbetler ---


def test_history_page_empty_when_no_past_chats(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.get("/asistan/gecmis")
    assert response.status_code == 200
    assert "Henüz geçmiş bir sohbetiniz yok." in response.text


def test_history_page_lists_past_session_with_preview_and_current_marker(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "merhaba dünya"})

    response = client.get("/asistan/gecmis")
    assert response.status_code == 200
    assert "merhaba dünya" in response.text
    assert "Şu an açık" in response.text
    assert "/asistan/sohbete-don/" not in response.text  # aktif oturum için dönüş butonu yok


def test_history_page_no_active_account_shows_empty(client):
    response = client.get("/asistan/gecmis")
    assert response.status_code == 200
    assert "Henüz geçmiş bir sohbetiniz yok." in response.text


def test_resume_chat_switches_active_session_and_old_stays_accessible(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/asistan/mesaj", data={"metin": "birinci sohbet"})
    first_session_id = client.cookies.get("chat_session")

    client.post("/asistan/yeni-sohbet")
    client.post("/asistan/mesaj", data={"metin": "ikinci sohbet"})
    second_session_id = client.cookies.get("chat_session")
    assert second_session_id != first_session_id

    response = client.post(f"/asistan/sohbete-don/{first_session_id}", follow_redirects=False)
    assert response.status_code == 303
    assert client.cookies.get("chat_session") == first_session_id

    page = client.get("/anasayfa")
    assert "birinci sohbet" in page.text
    assert "ikinci sohbet" not in page.text

    # Geçmiş listesinde artık ikinci sohbet için "dön" butonu, birincisi
    # için "şu an açık" görünmeli.
    history = client.get("/asistan/gecmis")
    assert f"/asistan/sohbete-don/{second_session_id}" in history.text


def test_resume_chat_rejects_session_belonging_to_another_account(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")

    client.post("/asistan/mesaj", data={"metin": "acc1 sohbeti"}, cookies={"active_account": "acc1"})
    acc1_session_id = client.cookies.get("chat_session")

    # Aktif hesabı acc2'ye çevirip acc1'in oturumuna dönmeyi dene.
    response = client.post(
        f"/asistan/sohbete-don/{acc1_session_id}",
        cookies={"active_account": "acc2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.cookies.get("chat_session") == acc1_session_id  # değişmedi, reddedildi


# --- Buton tıklamalarında görünen mesaj (bkz. canlı testte bulunan kusur:
# balon ham "approve" gösteriyordu, düğmenin kendi çevrilmiş etiketi değil) ---


def test_button_click_shows_translated_label_not_raw_action_value(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post(
        "/asistan/mesaj", data={"action": "approve"}, headers={"X-Requested-With": "fetch"}
    )
    assert response.status_code == 200
    assert "Onayla" in response.text
    assert ">approve<" not in response.text


def test_free_text_message_is_shown_verbatim(client):
    """Serbest metin (buton değil) dokunulmadan gösterilmeli — yalnızca
    action= dolu hızlı-yanıt tıklamaları çeviriye tabi."""
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post(
        "/asistan/mesaj", data={"metin": "approve etmek istiyorum"}, headers={"X-Requested-With": "fetch"}
    )
    assert response.status_code == 200
    assert "approve etmek istiyorum" in response.text
