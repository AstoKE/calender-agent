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

import json

import pytest
from fastapi.testclient import TestClient

from src.connectors.account_registry import ensure_account_registered
from src.providers.base import EmbeddingProvider, FileInputCapable, LLMProvider
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

    def get_freebusy(self, *args, **kwargs):
        return []

    def create_event(self, *args, **kwargs):
        return "fake-event-id"


_SINGLE_EVENT_FILE_RESPONSE = json.dumps([
    {"event_type": "meeting", "title": "Webinar: AI Trends", "start_datetime": "2026-09-01T14:00:00",
     "duration_minutes": 60, "location": None, "ambiguous_fields": []},
])


class _DummyVisionLLMProvider(LLMProvider, FileInputCapable):
    """FileInputCapable UYGULAYAN sahte — dosyadan etkinlik ekleme testleri
    (bkz. src/services/chat_flow.py::_dispatch_file_upload) için `client`
    fixture'ındaki `_DummyLLMProvider`'ın yerine geçer, gerçek Gemini API'yi
    HİÇ çağırmaz.

    `file_response` sınıf seviyesinde: app.py lifespan bu sınıfı KENDİSİ
    instantiate ettiği için (bkz. vision_client fixture) constructor'a
    senaryoya özel bir yanıt geçirmenin pratik bir yolu yok — testler bunun
    yerine `monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", ...)`
    ile isteğe özel bir JSON LİSTESİ (bkz. extract_candidate_events_from_file'ın
    beklediği sözleşme) verebilir."""

    file_response = _SINGLE_EVENT_FILE_RESPONSE

    def __init__(self, *args, **kwargs):
        pass

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}" if json_output else ""

    def generate_from_file(self, system_prompt, user_prompt, file_bytes, mime_type, json_output=False):
        return type(self).file_response

    def is_available(self):
        return True


@pytest.fixture
def client(temp_db, monkeypatch):
    # HEM FoundryLocal HEM Gemini dalını sahteyle değiştiriyoruz — bu makinenin
    # .env'inde LLM_PROVIDER=gemini set (canlı Gemini testleri için), yalnızca
    # FoundryLocalProvider'ı yamalamak bu ortamda SESSİZCE etkisiz kalıyordu
    # (app.py hâlâ gerçek GeminiProvider'ı kuruyordu — bulundu, testler bu
    # ortamdan bağımsız olmalı, hangi .env'de çalışırsa çalışsın).
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.app.GeminiProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.GeminiEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.chat_routes._get_calendar", lambda request, account_id: _NullCalendar())
    from src.ui.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def vision_client(temp_db, monkeypatch):
    """`client` ile AYNI ama her iki dalı da (FoundryLocal VE Gemini, bkz.
    client fixture'ındaki not) görsel/PDF girişini destekleyen bir sahteyle
    değiştiriyor — "Gemini backend'i etkin" durumunu gerçek API'ye hiç
    dokunmadan simüle eder."""
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyVisionLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.app.GeminiProvider", _DummyVisionLLMProvider)
    monkeypatch.setattr("src.ui.app.GeminiEmbeddingProvider", _DummyEmbeddingProvider)
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


def test_chat_uses_master_calendar_account_when_configured(client, monkeypatch):
    # bkz. src/connectors/account_registry.py::resolve_write_account_id —
    # aktif hesap acc1 ama "Ana takvim hesabı" acc2 olarak ayarlı, sohbetin
    # takvim çağrıları hep acc2'ye gitmeli.
    from src.storage.preferences import set_preference

    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    set_preference("calendar.master_account_id", "acc2")

    used_account_ids: list[str] = []
    monkeypatch.setattr(
        "src.ui.chat_routes._get_calendar",
        lambda request, account_id: used_account_ids.append(account_id) or _NullCalendar(),
    )

    # acc1 önce kaydedildiği için resolve_active_account'ın cookie'siz
    # fallback'i (bkz. src/ui/session.py) onu aktif hesap yapar.
    response = client.post(
        "/asistan/mesaj",
        data={"metin": "merhaba"},
        headers={"X-Requested-With": "fetch"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert used_account_ids == ["acc2"]


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


class _CreateEventLLMProvider(LLMProvider):
    """`classify_intent`'i CREATE_EVENT'e, çıkarımı geçerli bir tek-etkinlik
    JSON'una yönlendiren minimal sahte — `_dispatch_intent`'in CREATE_EVENT
    dalına gerçekten girmek için (aksi halde `_DummyLLMProvider`'ın hep "{}"
    dönmesi `classify_intent`'i OTHER'a düşürüp bu testin amaçladığı kod
    yolunu hiç tetiklemezdi)."""

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        if "niyetini sınıflandır" in system_prompt:
            return json.dumps({"intent": "create_event", "query_range_start": None, "query_range_end": None})
        return json.dumps({
            "event_type": "meeting", "title": "Test", "start_datetime": "2026-09-01T14:00:00",
            "duration_minutes": 30, "location": None, "ambiguous_fields": [],
        })

    def is_available(self):
        return True


def test_ajax_message_crash_shows_error_bubble_instead_of_silence(client, monkeypatch):
    # Regresyon: advance() içinde HERHANGİ beklenmeyen bir istisna (canlı
    # testte gerçek sebep bir model liste döndürüp eski kodun tek-nesne
    # bekleyen mantığının çökmesiydi — o kök neden artık ayrıca düzeltildi,
    # bkz. extract_candidate_events_from_text) önceden yalnızca log'a yazılıp
    # yutuluyordu — kullanıcının mesajı kaydediliyordu ama HİÇBİR yanıt
    # eklenmiyordu, sohbet sessizce takılı kalıyordu (bkz. chat_routes.py'nin
    # genel except bloğu). Burada extract sonrası çağrılan bir adımı
    # (apply_retrieved_policies) doğrudan bozarak, HANGİ istisna olursa olsun
    # bu güvenlik ağının çalıştığı doğrulanıyor.
    monkeypatch.setattr(
        "src.services.chat_flow.apply_retrieved_policies",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated failure")),
    )
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    from src.ui.app import app

    app.state.llm = _CreateEventLLMProvider()

    response = client.post(
        "/asistan/mesaj",
        data={"metin": "toplantı A yarın, toplantı B öbür gün"},
        headers={"X-Requested-With": "fetch"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "toplantı A yarın, toplantı B öbür gün" in response.text  # kullanıcı mesajı hâlâ görünüyor
    assert "Bir sorun oldu" in response.text  # chat.generic_error, sessiz değil


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


# --- Dosyadan (görsel/PDF) etkinlik ekleme (bkz. FileInputCapable, src/services/chat_flow.py::_dispatch_file_upload) ---


def test_attach_row_hidden_without_vision_capable_provider(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert 'name="dosya"' not in response.text


def test_attach_row_shown_with_vision_capable_provider(vision_client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.get("/anasayfa")
    assert response.status_code == 200
    assert 'name="dosya"' in response.text
    assert 'enctype="multipart/form-data"' in response.text


# --- Ses→metin (mikrofon) — bkz. plan "sesli konuşarak iletişim" ---


def test_mic_button_shown_with_vision_capable_provider(vision_client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.get("/anasayfa")
    assert response.status_code == 200
    assert 'id="mic-btn"' in response.text
    assert 'name="ses"' in response.text


def test_mic_button_hidden_without_vision_capable_provider(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert 'id="mic-btn"' not in response.text


def test_voice_message_transcribes_and_shows_transcript_as_user_bubble(vision_client, monkeypatch):
    monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", "yarın toplantı var mı diye soruyorum")
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = vision_client.post(
        "/asistan/mesaj",
        data={"next": "/anasayfa"},
        files={"ses": ("kayit.ogg", b"FAKE_AUDIO_BYTES", "audio/ogg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    # Transkript kullanıcı balonunda GÖRÜNÜYOR (jenerik bir "ses gönderildi"
    # yer tutucusu değil) — kullanıcı ne anlaşıldığını görebilsin diye.
    assert "yarın toplantı var mı diye soruyorum" in response.text


def test_voice_message_empty_transcription_shows_error(vision_client, monkeypatch):
    monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", "")
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = vision_client.post(
        "/asistan/mesaj",
        files={"ses": ("kayit.ogg", b"FAKE_AUDIO_BYTES", "audio/ogg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "anlayamadım" in response.text


def test_voice_message_without_vision_provider_shows_error_not_crash(client):
    # `client` (vision_client DEĞİL) — FoundryLocal sahtesi FileInputCapable
    # UYGULAMIYOR; mikrofon butonu normalde hiç gösterilmez ama doğrudan bir
    # POST (örn. eski bir sekme) sunucuyu ÇÖKERTMEMELİ, zarifçe düşmeli.
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post(
        "/asistan/mesaj",
        files={"ses": ("kayit.ogg", b"FAKE_AUDIO_BYTES", "audio/ogg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "anlayamadım" in response.text


def test_voice_only_message_is_not_treated_as_empty(vision_client, monkeypatch):
    monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", "merhaba")
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    response = vision_client.post(
        "/asistan/mesaj",
        files={"ses": ("kayit.ogg", b"FAKE_AUDIO_BYTES", "audio/ogg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert vision_client.cookies.get("chat_session")  # bir oturum açıldı, boş gönderimde açılmazdı


def test_file_upload_extracts_event_and_reaches_preview(vision_client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.post(
        "/asistan/mesaj",
        data={"next": "/anasayfa"},
        files={"dosya": ("davetiye.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "Webinar: AI Trends" in response.text
    assert "Dosya gönderildi" in response.text  # bilgi metni olmadan yollanan dosya icin placeholder balon


def test_file_upload_with_caption_shows_caption_not_placeholder(vision_client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.post(
        "/asistan/mesaj",
        data={"metin": "bu davetiyeyi ekle", "next": "/anasayfa"},
        files={"dosya": ("davetiye.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "bu davetiyeyi ekle" in response.text
    assert "Webinar: AI Trends" in response.text


def test_file_only_message_is_not_treated_as_empty(vision_client):
    """metin bos, action bos, yalnizca dosya var — bos gonderim gibi sessizce
    yok sayilmamali (bkz. chat_routes.py::send_chat_message has_file kontrolu)."""
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.post(
        "/asistan/mesaj",
        files={"dosya": ("davetiye.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert vision_client.cookies.get("chat_session")  # bir oturum acildi, bos gonderimde acilmazdi


def test_file_upload_unsupported_mime_type_shows_error(vision_client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.post(
        "/asistan/mesaj",
        files={"dosya": ("notes.txt", b"plain text", "text/plain")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "desteklenmiyor" in response.text


def test_file_upload_without_vision_provider_shows_error(client):
    """`client` (vision_client DEĞİL) — FoundryLocal sahtesi FileInputCapable
    DEĞİL, kullanıcıya nedenini soylemeli, sessizce yutmamali."""
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post(
        "/asistan/mesaj",
        files={"dosya": ("davetiye.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "Gemini" in response.text


# --- Çoklu-etkinlik: bir dosyada birden fazla ayrı etkinlik (bkz. canlı testte
# bulunan senaryo — bir takvim ekran görüntüsünde iki toplantı, model bir JSON
# LİSTESİ döndürdü) — ChatState.queued_candidates/_finish_candidate akışı ---


_TWO_EVENT_FILE_RESPONSE = json.dumps([
    {"event_type": "meeting", "title": "Likidite ve Yatırım Portföy Yönetimi",
     "start_datetime": "2026-08-25T14:00:00", "duration_minutes": 60,
     "location": None, "ambiguous_fields": []},
    {"event_type": "meeting", "title": "Bankacılıkta Yatırım Hizmetleri",
     "start_datetime": "2026-08-25T15:30:00", "duration_minutes": 60,
     "location": None, "ambiguous_fields": []},
])


def test_file_upload_with_two_events_shows_first_with_batch_progress(vision_client, monkeypatch):
    monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", _TWO_EVENT_FILE_RESPONSE)
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = vision_client.post(
        "/asistan/mesaj",
        files={"dosya": ("program.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "1/2" in response.text
    assert "Likidite ve Yatırım Portföy Yönetimi" in response.text
    assert "Bankacılıkta Yatırım Hizmetleri" not in response.text  # ikincisi henüz sırada


def test_approving_first_of_two_events_automatically_starts_second(vision_client, monkeypatch):
    monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", _TWO_EVENT_FILE_RESPONSE)
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    vision_client.post(
        "/asistan/mesaj",
        files={"dosya": ("program.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        headers={"X-Requested-With": "fetch"},
    )

    response = vision_client.post(
        "/asistan/mesaj", data={"action": "approve"}, headers={"X-Requested-With": "fetch"}
    )
    assert response.status_code == 200
    assert "2/2" in response.text
    assert "Bankacılıkta Yatırım Hizmetleri" in response.text


def test_approving_last_of_two_events_finishes_normally(vision_client, monkeypatch):
    monkeypatch.setattr(_DummyVisionLLMProvider, "file_response", _TWO_EVENT_FILE_RESPONSE)
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    vision_client.post(
        "/asistan/mesaj",
        files={"dosya": ("program.jpg", b"FAKE_JPEG_BYTES", "image/jpeg")},
        headers={"X-Requested-With": "fetch"},
    )
    vision_client.post("/asistan/mesaj", data={"action": "approve"}, headers={"X-Requested-With": "fetch"})

    response = vision_client.post(
        "/asistan/mesaj", data={"action": "approve"}, headers={"X-Requested-With": "fetch"}
    )
    assert response.status_code == 200
    assert response.text.count("Takvime eklendi.") == 2  # her iki etkinlik de onaylandı

    session_id = vision_client.cookies.get("chat_session")
    with get_connection() as conn:
        step = conn.execute("SELECT step FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()["step"]
    assert step is None  # sohbet idle durumuna döndü, sırada bekleyen kalmadı
