"""Web UI route'ları için scripted doğrulama (bkz. docs/architecture-plan.md §16).

FoundryLocalProvider/FoundryLocalEmbeddingProvider gerçek modelleri yükler
(yavaş, donanım gerektirir) — lifespan içinde çağrıldıkları için burada
gerçek LLMProvider/EmbeddingProvider arayüzünü uygulayan sahtelerle
değiştiriliyor (sadece varlığı değil, `embed()`/`generate()` çağrılabilirliği
de gerekiyor — sonraki dilimlerde Kurallarım'ın "yeni kural" formu gibi
route'lar embedding_provider'ı gerçekten çağıracak). temp_db fixture'ı
gerçek data/calendar_agent.db'ye dokunulmasını engelliyor.

Assertion'lar KASITLI olarak yapıya (durum kodu, CSS sınıfı, href) bakıyor,
tam Türkçe kopyaya değil — i18n (bkz. plan) metinleri değiştirecek, bu
testlerin o değişiklikte kırılmaması gerekiyor."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.candidates.store import save_new_candidate
from src.connectors.account_registry import ensure_account_registered
from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.memory.correction_memory import mark_correction_approved, save_user_correction
from src.policies.store import add_policy, get_policy
from src.providers.base import EmbeddingProvider, LLMProvider
from src.storage.db import get_connection

from conftest import login_test_client


class _DummyLLMProvider(LLMProvider):
    def __init__(self, *args, **kwargs):
        pass

    def generate(
        self,
        system_prompt,
        user_prompt,
        context_chunks=None,
        json_output=False,
        allow_thinking=False,
    ):
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
def client(temp_db, monkeypatch):
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    from src.ui.app import app

    with TestClient(app) as test_client:
        login_test_client(test_client)
        yield test_client


def _persisted_correction(feedback: str = "yanlış süre", **correction_kwargs):
    """Düzeltmelerim route testleri için: hesap+mail+candidate zincirini
    kurup gerçek bir UserCorrection kaydeder (user_corrections.candidate_id
    candidate_events'e FK veriyor, bkz. test_correction_memory.py'deki
    aynı desen)."""
    account_id = "acc-" + str(uuid.uuid4())[:8]
    email_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (id, provider, account_type, email, connected_at, status) "
            "VALUES (?, 'google', 'personal', ?, ?, 'active')",
            (account_id, f"{account_id}@example.com", now),
        )
        conn.execute(
            "INSERT INTO email_threads (thread_id, account_id, participants, languages_seen, last_message_at) "
            "VALUES (?,?,?,?,?)",
            (f"thread-{email_id}", account_id, "[]", "[]", now),
        )
        conn.execute(
            """
            INSERT INTO email_messages (
                id, account_id, provider, message_id, thread_id, subject, sender,
                recipients, received_at, detected_language, body_excerpt, labels, processed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (email_id, account_id, "gmail", f"msg-{email_id}", f"thread-{email_id}", "Test mail",
             "alerts@example.com", "[]", now, "tr", "", "[]"),
        )
    candidate = CandidateEvent(
        candidate_id=str(uuid.uuid4()),
        source_type=SourceType.EMAIL,
        event_type=EventType.MEETING,
        title="Proje toplantısı",
        start_datetime=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        duration_minutes=60,
        status=CandidateStatus.READY_FOR_CONFIRMATION,
        extraction_reason="test",
    )
    save_new_candidate(candidate, source_email_row_id=email_id)
    return save_user_correction(candidate, feedback, **correction_kwargs)


def _pending_candidate_for_account(account_id: str, title: str = "Proje toplantısı") -> None:
    """Bildirim çanı/rozet testleri için: `_persisted_correction`'ın ilk
    yarısı — hesap+mail+candidate zincirini kurar ama düzeltme kaydetmez,
    hesap zaten ensure_account_registered ile kayıtlı olmalı (email_messages.
    account_id -> accounts.id FK'si var, INSERT OR IGNORE bunu tolere eder)."""
    email_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO email_threads (thread_id, account_id, participants, languages_seen, last_message_at) "
            "VALUES (?,?,?,?,?)",
            (f"thread-{email_id}", account_id, "[]", "[]", now),
        )
        conn.execute(
            """
            INSERT INTO email_messages (
                id, account_id, provider, message_id, thread_id, subject, sender,
                recipients, received_at, detected_language, body_excerpt, labels, processed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (email_id, account_id, "gmail", f"msg-{email_id}", f"thread-{email_id}", "Test mail",
             "alerts@example.com", "[]", now, "tr", "", "[]"),
        )
    candidate = CandidateEvent(
        candidate_id=str(uuid.uuid4()),
        source_type=SourceType.EMAIL,
        event_type=EventType.MEETING,
        title=title,
        start_datetime=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        duration_minutes=60,
        status=CandidateStatus.READY_FOR_CONFIRMATION,
        extraction_reason="test",
    )
    save_new_candidate(candidate, source_email_row_id=email_id)


def test_root_redirects_to_anasayfa(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/anasayfa"


def test_unknown_page_is_404(client):
    response = client.get("/bilinmeyen-sayfa")
    assert response.status_code == 404
    assert "<h1>" in response.text  # markalı 404 sayfası, boş gövde değil


def test_hesaplar_lists_registered_accounts(client):
    ensure_account_registered("test_hesap", provider="google", email="test@example.com")
    response = client.get("/hesaplar")
    assert response.status_code == 200
    assert "test@example.com" in response.text
    assert 'class="card"' in response.text


def test_hesaplar_empty_state_when_no_accounts(client):
    response = client.get("/hesaplar")
    assert response.status_code == 200
    assert 'class="empty-state"' in response.text
    assert 'class="card"' not in response.text


@pytest.mark.parametrize(
    "page,url",
    [
        ("oneriler", "/oneriler"),
        ("hesaplar", "/hesaplar"),
        ("takvim", "/takvim"),
    ],
)
def test_sidebar_nav_present_and_highlights_active_page(client, page, url):
    response = client.get(url)
    assert response.status_code == 200
    assert 'href="/oneriler"' in response.text
    assert 'href="/hesaplar"' in response.text
    assert 'class="active"' in response.text


# --- Faz 2: dil / hesap değiştirme / CSRF guard ---


def test_pages_render_with_no_lang_cookie_default_to_tr(client):
    response = client.get("/oneriler")
    assert response.status_code == 200
    assert '<html lang="tr">' in response.text


def test_set_language_persists_via_cookie_and_changes_rendered_text(client):
    response = client.post("/dil", data={"dil": "en", "next": "/oneriler"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/oneriler"
    assert response.cookies.get("ui_lang") == "en"

    followed = client.get("/oneriler")
    assert '<html lang="en">' in followed.text


def test_set_language_unsafe_next_falls_back_to_anasayfa(client):
    response = client.post(
        "/dil", data={"dil": "tr", "next": "//evil.example.com"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa"


# --- Tema (bkz. src/ui/session.py resolve_theme, tokens.css) ---


def test_pages_render_without_data_theme_when_no_cookie_set(client):
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert '<html lang="tr">' in response.text
    assert "data-theme" not in response.text


def test_set_theme_dark_persists_via_cookie_and_sets_data_theme_attribute(client):
    response = client.post("/tema", data={"tema": "dark", "next": "/oneriler"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/oneriler"
    assert response.cookies.get("ui_theme") == "dark"

    followed = client.get("/oneriler")
    assert '<html lang="tr" data-theme="dark">' in followed.text


def test_set_theme_light_sets_data_theme_attribute(client):
    client.post("/tema", data={"tema": "light", "next": "/anasayfa"})
    response = client.get("/anasayfa")
    assert '<html lang="tr" data-theme="light">' in response.text


def test_set_theme_back_to_system_removes_data_theme_attribute(client):
    client.post("/tema", data={"tema": "dark", "next": "/anasayfa"})
    client.post("/tema", data={"tema": "system", "next": "/anasayfa"})
    response = client.get("/anasayfa")
    assert "data-theme" not in response.text


def test_set_theme_invalid_value_falls_back_to_system(client):
    response = client.post("/tema", data={"tema": "purple", "next": "/anasayfa"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.cookies.get("ui_theme") == "system"


def test_select_unknown_account_does_not_set_cookie(client):
    response = client.post(
        "/hesap-sec", data={"account_id": "bilinmeyen-id", "next": "/oneriler"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "active_account" not in response.cookies


def test_select_known_account_sets_cookie_and_shows_active(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    response = client.post(
        "/hesap-sec", data={"account_id": "acc2", "next": "/oneriler"}, follow_redirects=False
    )
    assert response.cookies.get("active_account") == "acc2"

    followed = client.get("/oneriler")
    assert "b@example.com" in followed.text


def test_stale_account_cookie_self_heals(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    # Jar'a değil, tek bir istek başlığına koyuyoruz — jar'a koymak httpx'in
    # cookie domain eşleştirmesiyle testte yapay bir CookieConflict'e yol
    # açıyor (üretimde tek bir origin olduğu için gerçek bir sorun değil).
    response = client.get("/oneriler", cookies={"active_account": "silinmis-hesap"})
    assert response.status_code == 200
    assert response.cookies.get("active_account") == "acc1"


def test_cross_site_post_is_rejected(client):
    response = client.post("/dil", data={"dil": "tr"}, headers={"sec-fetch-site": "cross-site"})
    assert response.status_code == 403


def test_same_origin_post_is_allowed(client):
    response = client.post(
        "/dil", data={"dil": "tr"}, headers={"sec-fetch-site": "same-origin"}, follow_redirects=False
    )
    assert response.status_code == 303


# --- Faz 5: Takvim — bozulma matrisinin dört durumu, hepsi HTTP 200 ---


class _FakeCalendar:
    def __init__(self, events=None, error=None):
        self._events = events or []
        self._error = error
        self.list_events_call_count = 0

    def list_events(self, time_min, time_max, calendar_id="primary"):
        self.list_events_call_count += 1
        if self._error:
            raise self._error
        return self._events


def test_takvim_no_account_state(client):
    response = client.get("/takvim")
    assert response.status_code == 200
    assert 'class="empty-state"' in response.text


def test_takvim_no_token_state(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/takvim")
    assert response.status_code == 200
    assert 'class="warning"' in response.text


def test_takvim_error_state_does_not_500(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    fake = _FakeCalendar(error=RuntimeError("boom"))
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: fake)
    response = client.get("/takvim")
    assert response.status_code == 200
    assert 'class="warning"' in response.text
    assert "boom" in response.text


def test_takvim_ok_state_renders_events(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    fake = _FakeCalendar(
        events=[
            {
                "id": "evt1",
                "summary": "Proje Toplantısı",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-19T14:00:00+03:00"},
                "end": {"dateTime": "2026-08-19T15:00:00+03:00"},
            }
        ]
    )
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: fake)
    response = client.get("/takvim?hafta=2026-08-17")
    assert response.status_code == 200
    assert "Proje Toplantısı" in response.text
    # Saat-ızgarası (bkz. src/services/calendar_view.py layout_timed_entries) —
    # eski basit "calendar-grid" listesinin yerini aldı.
    assert 'class="timegrid__entry"' in response.text
    assert 'class="timegrid-wrap"' in response.text


def test_takvim_reload_same_week_uses_cache_not_live_api(client, monkeypatch):
    # bkz. src/services/calendar_cache.py: kısa ömürlü write-through cache —
    # aynı hafta için art arda gelen istekler canlı API'ye ikinci kez gitmemeli.
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    fake = _FakeCalendar(events=[])
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: fake)

    client.get("/takvim?hafta=2026-08-17")
    client.get("/takvim?hafta=2026-08-17")

    assert fake.list_events_call_count == 1


def test_takvim_invalid_week_param_falls_back_without_500(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    fake = _FakeCalendar(events=[])
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: fake)
    response = client.get("/takvim?hafta=not-a-date")
    assert response.status_code == 200


# --- Faz 6: Ana Sayfa ---


def test_anasayfa_no_account_state(client):
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert 'class="empty-state"' in response.text
    assert 'action="/tara"' not in response.text  # hesap yokken tarama CTA'sı yok


def test_anasayfa_with_account_shows_greeting_and_scan_cta(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert "a@example.com" in response.text
    assert 'action="/tara"' in response.text


def test_anasayfa_calendar_unavailable_does_not_500(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    fake = _FakeCalendar(error=RuntimeError("boom"))
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: fake)
    response = client.get("/anasayfa")
    assert response.status_code == 200


def test_anasayfa_shows_assistant_chat_with_form(client, monkeypatch):
    # Web Chatbox (Faz 5) CHAT_ENABLED=True yaptı — _asistan_slot.html'in
    # yerini gerçek bir sohbet formu aldı (bkz. plan).
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert 'class="assistant-chat"' in response.text
    assert 'action="/asistan/mesaj"' in response.text


def test_anasayfa_pending_preview_uses_same_card_as_oneriler(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert 'class="empty-state"' in response.text  # bekleyen öneri yok durumu


def test_anasayfa_scan_busy_shows_notice(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/anasayfa?mesgul=1")
    assert response.status_code == 200
    assert 'class="warning"' in response.text


# --- Bildirim çanı / rozetler (bkz. src/ui/templating.py shell_context) ---


def test_notification_bell_empty_state_when_no_pending(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert "notif-badge" not in response.text
    assert "nav-badge" not in response.text
    assert 'class="info-banner"' not in response.text


def test_notification_bell_shows_badge_and_preview_with_pending(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    _pending_candidate_for_account("acc1", title="Webinar Daveti")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)

    response = client.get("/anasayfa")
    assert response.status_code == 200
    assert '<span class="notif-badge">1</span>' in response.text
    assert '<span class="nav-badge">1</span>' in response.text
    assert 'class="notif-item"' in response.text
    assert "Webinar Daveti" in response.text
    assert 'class="info-banner"' in response.text


def test_notification_bell_appears_on_every_page(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    _pending_candidate_for_account("acc1")
    response = client.get("/kurallarim")
    assert response.status_code == 200
    assert "#icon-bell" in response.text
    assert '<span class="notif-badge">1</span>' in response.text


def test_notification_badge_caps_at_nine_plus(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    for i in range(10):
        _pending_candidate_for_account("acc1", title=f"Etkinlik {i}")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda request, account_id: None)
    response = client.get("/anasayfa")
    assert '<span class="notif-badge">9+</span>' in response.text
    assert '<span class="nav-badge">9+</span>' in response.text
    # panel yalnızca ilk 5'i onizlemeli, tumunu degil
    assert response.text.count('class="notif-item"') == 5


def test_tara_active_account_single_flight_guard(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")

    def _fake_scan(account_id, llm, embedding_provider):
        assert account_id in client.app.state.scan_in_progress
        return {"total": 0, "candidates_found": 0, "skipped_errors": 0}

    monkeypatch.setattr("src.ui.routes.scan_account_inbox", _fake_scan)
    response = client.post("/tara", follow_redirects=False)
    assert response.status_code == 303
    assert "tarandi" in response.headers["location"]
    assert client.app.state.scan_in_progress == set()


def test_tara_no_active_account_redirects_home(client):
    response = client.post("/tara", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa"


# --- Faz 7: Kurallarım ---


def test_kurallarim_empty_state(client):
    response = client.get("/kurallarim")
    assert response.status_code == 200
    assert 'class="empty-state"' in response.text


def test_kurallarim_lists_active_policy(client, temp_db):
    add_policy(
        "default_duration_minutes", "Toplantılar 45 dakika", {"default_duration_minutes": 45}, event_type="meeting",
        user_id=client.test_user["id"],
    )
    response = client.get("/kurallarim")
    assert response.status_code == 200
    assert "Toplantılar 45 dakika" in response.text
    assert 'class="card"' in response.text


def test_kurallarim_inactive_policy_is_collapsed(client, temp_db):
    p = add_policy("importance", "eski kural", {"importance": "high"}, event_type="meeting", user_id=client.test_user["id"])
    client.post(f"/kurallarim/{p.policy_id}/pasiflestir")
    response = client.get("/kurallarim")
    assert response.status_code == 200
    assert 'class="inactive-policies"' in response.text
    assert "eski kural" in response.text


def test_deactivate_rule_route(client, temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting", user_id=client.test_user["id"])
    response = client.post(f"/kurallarim/{p.policy_id}/pasiflestir", follow_redirects=False)
    assert response.status_code == 303
    assert get_policy(p.policy_id).active is False


def test_deactivate_unknown_rule_does_not_500(client):
    response = client.post("/kurallarim/olmayan-id/pasiflestir", follow_redirects=False)
    assert response.status_code == 303


def test_reactivate_rule_route(client, temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting", user_id=client.test_user["id"])
    client.post(f"/kurallarim/{p.policy_id}/pasiflestir")
    response = client.post(f"/kurallarim/{p.policy_id}/aktiflestir", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/kurallarim"
    assert get_policy(p.policy_id).active is True


def test_reactivate_conflict_redirects_with_flag(client, temp_db):
    p1 = add_policy("importance", "eski", {"importance": "high"}, event_type="meeting", user_id=client.test_user["id"])
    client.post(f"/kurallarim/{p1.policy_id}/pasiflestir")
    add_policy("importance", "yeni", {"importance": "low"}, event_type="meeting", user_id=client.test_user["id"])

    response = client.post(f"/kurallarim/{p1.policy_id}/aktiflestir", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/kurallarim?cakisma=1"

    followed = client.get("/kurallarim?cakisma=1")
    assert 'class="warning"' in followed.text


def test_new_rule_form_renders(client):
    response = client.get("/kurallarim/yeni")
    assert response.status_code == 200
    assert '<form method="post" action="/kurallarim/yeni"' in response.text


def test_new_rule_submit_creates_duration_policy(client, temp_db):
    response = client.post(
        "/kurallarim/yeni",
        data={
            "natural_language_rule": "Sınavlar 90 dakika",
            "scope_type": "event_type",
            "event_type": "exam",
            "action_type": "duration",
            "duration_minutes": "90",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/kurallarim"

    listing = client.get("/kurallarim")
    assert "Sınavlar 90 dakika" in listing.text


def test_new_rule_submit_missing_value_redirects_with_error(client):
    response = client.post(
        "/kurallarim/yeni",
        data={"natural_language_rule": "boş kural", "action_type": "duration", "duration_minutes": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/kurallarim/yeni?hata=eksik"

    followed = client.get("/kurallarim/yeni?hata=eksik")
    assert 'class="warning"' in followed.text


def test_new_rule_submit_sender_scope(client, temp_db):
    response = client.post(
        "/kurallarim/yeni",
        data={
            "natural_language_rule": "LinkedIn mailleri düşük önemli",
            "scope_type": "sender",
            "sender": "alerts@linkedin.com",
            "action_type": "importance",
            "importance": "low",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = client.get("/kurallarim")
    assert "LinkedIn mailleri düşük önemli" in listing.text


def test_new_rule_natural_language_no_concrete_action_redirects_with_error(client):
    # _DummyLLMProvider.generate(json_output=True) "{}" döner -> hiçbir alan
    # çıkarılamaz -> derive_and_save_policy None döner.
    response = client.post(
        "/kurallarim/yeni-dogal-dil", data={"rule_text": "bir şey"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/kurallarim/yeni?hata=llm"


def test_new_rule_natural_language_success(client):
    class _DurationLLM(_DummyLLMProvider):
        def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
            return '{"event_type": "exam", "default_duration_minutes": 90}' if json_output else ""

    client.app.state.llm = _DurationLLM()
    response = client.post(
        "/kurallarim/yeni-dogal-dil", data={"rule_text": "Sınavlar her zaman 90 dakika"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/kurallarim"

    listing = client.get("/kurallarim")
    assert "Sınavlar her zaman 90 dakika" in listing.text


def test_kural_yeni_form_has_both_modes(client):
    response = client.get("/kurallarim/yeni")
    assert response.status_code == 200
    assert 'action="/kurallarim/yeni-dogal-dil"' in response.text
    assert 'action="/kurallarim/yeni"' in response.text


# --- Faz 8: Düzeltmelerim ---


def test_duzeltmelerim_empty_state(client):
    response = client.get("/duzeltmelerim")
    assert response.status_code == 200
    assert 'class="empty-state"' in response.text


def test_duzeltmelerim_lists_correction_with_no_diff_message(client, temp_db):
    _persisted_correction("yanlış süre", user_id=client.test_user["id"])
    response = client.get("/duzeltmelerim")
    assert response.status_code == 200
    assert "yanlış süre" in response.text
    assert 'class="diff-table"' not in response.text  # original==corrected -> gerçek diff yok


def test_duzeltmelerim_shows_diff_table_when_snapshots_differ(client, temp_db):
    _persisted_correction(
        "süre düzeltildi",
        original_output={"title": "Toplantı", "duration_minutes": 30},
        corrected_output={"title": "Toplantı", "duration_minutes": 60},
        user_id=client.test_user["id"],
    )
    response = client.get("/duzeltmelerim")
    assert response.status_code == 200
    assert 'class="diff-table"' in response.text


def test_duzeltmelerim_classification_correction_shows_note_not_diff(client, temp_db):
    _persisted_correction("hiç takvimlik değildi", correction_type="classification", user_id=client.test_user["id"])
    response = client.get("/duzeltmelerim")
    assert response.status_code == 200
    assert 'class="diff-table"' not in response.text


def test_duzeltmelerim_filter_by_type(client, temp_db):
    # Not: geri bildirim metinleri kasıtlı benzersiz — "alan"/"sınıflandırma"
    # gibi genel kelimeler filtre çipi etiketleriyle (örn. "Alan düzeltmesi")
    # çakışıp yanlış pozitif/negatif üretir.
    _persisted_correction("FIELDCORRECTIONTEXT", user_id=client.test_user["id"])
    _persisted_correction("CLASSIFCORRECTIONTEXT", correction_type="classification", user_id=client.test_user["id"])

    all_response = client.get("/duzeltmelerim")
    assert "FIELDCORRECTIONTEXT" in all_response.text and "CLASSIFCORRECTIONTEXT" in all_response.text

    field_only = client.get("/duzeltmelerim?tur=alan")
    assert "FIELDCORRECTIONTEXT" in field_only.text and "CLASSIFCORRECTIONTEXT" not in field_only.text

    classification_only = client.get("/duzeltmelerim?tur=siniflandirma")
    assert "CLASSIFCORRECTIONTEXT" in classification_only.text and "FIELDCORRECTIONTEXT" not in classification_only.text


def test_duzeltmelerim_shows_derived_rule_link(client, temp_db):
    correction = _persisted_correction("kural", user_id=client.test_user["id"])
    policy = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting", user_id=client.test_user["id"])
    mark_correction_approved(correction.correction_id, policy.policy_id)

    response = client.get("/duzeltmelerim")
    assert response.status_code == 200
    assert 'href="/kurallarim"' in response.text


def test_toggle_future_use_off_deactivates_derived_policy(client, temp_db):
    correction = _persisted_correction("kural", user_id=client.test_user["id"])
    policy = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting", user_id=client.test_user["id"])
    mark_correction_approved(correction.correction_id, policy.policy_id)

    response = client.post(
        f"/duzeltmelerim/{correction.correction_id}/gelecekte-kullan",
        data={"enabled": "false"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert get_policy(policy.policy_id).active is False


def test_delete_correction_route(client, temp_db):
    correction = _persisted_correction("silinecek")
    response = client.post(f"/duzeltmelerim/{correction.correction_id}/sil", follow_redirects=False)
    assert response.status_code == 303

    listing = client.get("/duzeltmelerim")
    assert "silinecek" not in listing.text


def test_delete_unknown_correction_does_not_500(client):
    response = client.post("/duzeltmelerim/olmayan-id/sil", follow_redirects=False)
    assert response.status_code == 303


# --- Faz 9: Ayarlar ---


def test_ayarlar_renders_with_no_account(client):
    response = client.get("/ayarlar")
    assert response.status_code == 200
    assert 'action="/ayarlar/bolge"' not in response.text  # hesap yokken saat dilimi formu yok


def test_ayarlar_shows_timezone_form_with_account(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.get("/ayarlar")
    assert response.status_code == 200
    assert 'action="/ayarlar/bolge"' in response.text
    assert "Europe/Istanbul" in response.text  # varsayılan seçili


def test_ayarlar_shows_diagnostics(client):
    response = client.get("/ayarlar")
    assert "<code>" in response.text
    assert "qwen3-4b" in response.text


def test_ayarlar_shows_master_calendar_form_with_accounts(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    response = client.get("/ayarlar")
    assert response.status_code == 200
    assert 'action="/ayarlar/ana-takvim"' in response.text
    assert "b@example.com" in response.text


def test_set_master_calendar_persists_and_shows_selected(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")

    response = client.post("/ayarlar/ana-takvim", data={"hesap": "acc2"}, follow_redirects=False)
    assert response.status_code == 303

    followed = client.get("/ayarlar")
    assert 'value="acc2" selected' in followed.text


def test_set_master_calendar_empty_resets_to_none(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    client.post("/ayarlar/ana-takvim", data={"hesap": "acc1"})

    response = client.post("/ayarlar/ana-takvim", data={"hesap": ""}, follow_redirects=False)
    assert response.status_code == 303

    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM user_preferences WHERE preference_key = 'calendar.master_account_id'"
        ).fetchone()
    assert row is None or row["value"] == "null"


def test_set_timezone_persists(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = client.post("/ayarlar/bolge", data={"saat_dilimi": "Europe/London"}, follow_redirects=False)
    assert response.status_code == 303

    followed = client.get("/ayarlar")
    assert 'value="Europe/London" selected' in followed.text


def test_set_timezone_without_account_does_not_500(client):
    response = client.post("/ayarlar/bolge", data={"saat_dilimi": "UTC"}, follow_redirects=False)
    assert response.status_code == 303


def test_ayarlar_language_form_reuses_dil_endpoint(client):
    response = client.get("/ayarlar")
    assert 'action="/dil"' in response.text
    assert 'value="/ayarlar"' in response.text  # next hedefi Ayarlar'a geri dönüyor


def test_language_persists_via_user_preferences_when_no_account(client):
    """Hesap yokken dil tercihi cookie silinse bile user_preferences'tan
    hayatta kalmalı (bkz. src/ui/session.py::resolve_language 3. katman)."""
    client.post("/dil", data={"dil": "en", "next": "/ayarlar"})
    client.cookies.delete("ui_lang")

    response = client.get("/anasayfa")
    assert '<html lang="en">' in response.text


# --- Mail-kaynaklı UPDATE_SUGGESTED (bkz. docs/architecture-plan.md §8.3) ---


class _UpdateTrackingCalendar:
    """Onayla/reddet route'larının create_event/update_event'ten HANGİSİNİ
    çağırdığını ayırt etmek için — bkz. src/services/chat_flow.py FakeCalendar
    ile aynı desen."""

    def __init__(self):
        self.created_events: list[dict] = []
        self.updated_events: list[tuple[str, dict]] = []

    def list_events(self, time_min, time_max, calendar_id="primary"):
        return []

    def get_freebusy(self, time_min, time_max, calendar_id="primary"):
        return []

    def create_event(self, *, title, start, end, location=None, calendar_id="primary"):
        self.created_events.append({"summary": title, "start": start, "end": end, "location": location})
        return "evt-new-1"

    def update_event(self, event_id, *, title=None, start=None, end=None, location=None, calendar_id="primary"):
        self.updated_events.append((event_id, {"summary": title, "start": start, "end": end, "location": location}))


def _update_suggested_candidate(account_id: str) -> str:
    """Onaylanmış (takvime yazılmış) bir candidate'ı, aynı thread'deki bir
    yanıt maili üzerinden UPDATE_SUGGESTED durumuna geçirir — approve/reject
    route'larının bu durumdaki dallanmasını test etmek için. Döner:
    candidate_id."""
    from src.candidates.store import apply_update_suggestion, set_candidate_google_event_id

    email_id = str(uuid.uuid4())
    reply_email_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO email_threads (thread_id, account_id, participants, languages_seen, last_message_at) "
            "VALUES (?,?,?,?,?)",
            ("thread-shared", account_id, "[]", "[]", now),
        )
        for eid in (email_id, reply_email_id):
            conn.execute(
                """
                INSERT INTO email_messages (
                    id, account_id, provider, message_id, thread_id, subject, sender,
                    recipients, received_at, detected_language, body_excerpt, labels, processed
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
                """,
                (eid, account_id, "gmail", f"msg-{eid}", "thread-shared", "Test mail",
                 "alerts@example.com", "[]", now, "tr", "", "[]"),
            )
    candidate = CandidateEvent(
        candidate_id=str(uuid.uuid4()),
        source_type=SourceType.EMAIL,
        event_type=EventType.MEETING,
        title="Proje toplantısı",
        start_datetime=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        duration_minutes=60,
        location="Oda 101",
        status=CandidateStatus.ADDED_TO_CALENDAR,
        extraction_reason="test",
    )
    save_new_candidate(candidate, source_email_row_id=email_id)
    set_candidate_google_event_id(candidate.candidate_id, "evt-original-1")
    apply_update_suggestion(candidate.candidate_id, {"location": "Oda 202"}, reply_email_id)
    return candidate.candidate_id


def test_approve_uses_master_calendar_account_when_configured(client, monkeypatch):
    # bkz. src/connectors/account_registry.py::resolve_write_account_id —
    # candidate acc1'in mailinden geldi ama "Ana takvim hesabı" acc2 olarak
    # ayarlı, bu yüzden onaylanınca acc2'nin takvimine yazılmalı.
    from src.storage.preferences import set_preference

    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    _pending_candidate_for_account("acc1")
    set_preference("calendar.master_account_id", "acc2", user_id=client.test_user["id"])

    with get_connection() as conn:
        candidate_id = conn.execute("SELECT candidate_id FROM candidate_events").fetchone()["candidate_id"]

    used_account_ids: list[str] = []

    class _RecordingCalendar:
        def get_freebusy(self, *args, **kwargs):
            return []

        def create_event(self, *, title, start, end, location=None, calendar_id="primary"):
            return "evt-1"

    def fake_get_calendar(request, account_id):
        used_account_ids.append(account_id)
        return _RecordingCalendar()

    monkeypatch.setattr("src.ui.routes._get_calendar", fake_get_calendar)

    response = client.post(f"/oneriler/{candidate_id}/onayla", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert used_account_ids == ["acc2"]


def test_approve_update_suggested_calls_update_event_not_create(client, monkeypatch):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    candidate_id = _update_suggested_candidate("acc1")

    calendar = _UpdateTrackingCalendar()
    monkeypatch.setattr("src.ui.routes._get_calendar", lambda request, account_id: calendar)

    response = client.post(f"/oneriler/{candidate_id}/onayla", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert calendar.created_events == []
    assert len(calendar.updated_events) == 1
    assert calendar.updated_events[0][0] == "evt-original-1"

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM candidate_events WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
    assert row["status"] == "UPDATED_IN_CALENDAR"


def test_reject_update_suggested_reverts_instead_of_rejecting(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    candidate_id = _update_suggested_candidate("acc1")

    response = client.post(f"/oneriler/{candidate_id}/reddet", data={}, follow_redirects=False)
    assert response.status_code == 303

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, location, previous_snapshot FROM candidate_events WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    assert row["status"] == "ADDED_TO_CALENDAR"
    assert row["location"] == "Oda 101"
    assert row["previous_snapshot"] is None


def test_oneriler_shows_update_suggested_badge_and_diff(client):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    _update_suggested_candidate("acc1")

    response = client.get("/oneriler")
    assert response.status_code == 200
    assert "diff-table" in response.text
    assert "Oda 101" in response.text
    assert "Oda 202" in response.text


# --- Per-user isolation (bkz. plan "Per-user isolation for rules, corrections, and suggestions") ---
# İki FARKLI logged-in kullanıcı, ayrı TestClient'lar üzerinden AYNI app/DB'ye
# karşı çalışıyor — biri diğerinin kurallarını/düzeltmelerini/önerilerini
# görmemeli.


def _second_client(temp_db, monkeypatch) -> TestClient:
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    from src.ui.app import app

    other_client = TestClient(app)
    login_test_client(other_client, email="other@example.com")
    return other_client


def test_kurallarim_isolated_between_users(client, temp_db, monkeypatch):
    add_policy(
        "importance", "kullanici A nin kurali", {"importance": "high"}, event_type="meeting",
        user_id=client.test_user["id"],
    )
    other = _second_client(temp_db, monkeypatch)

    assert "kullanici A nin kurali" in client.get("/kurallarim").text
    assert "kullanici A nin kurali" not in other.get("/kurallarim").text


def test_duzeltmelerim_isolated_between_users(client, temp_db, monkeypatch):
    _persisted_correction("A NIN DUZELTMESI", user_id=client.test_user["id"])
    other = _second_client(temp_db, monkeypatch)

    assert "A NIN DUZELTMESI" in client.get("/duzeltmelerim").text
    assert "A NIN DUZELTMESI" not in other.get("/duzeltmelerim").text


def test_oneriler_isolated_between_users(client, temp_db, monkeypatch):
    other = _second_client(temp_db, monkeypatch)

    account_id = "acc-user-a"
    ensure_account_registered(account_id, provider="google", email="owned-by-a@example.com", user_id=client.test_user["id"])
    _pending_candidate_for_account(account_id, title="A nin onerisi")

    assert "A nin onerisi" in client.get("/oneriler").text
    assert "A nin onerisi" not in other.get("/oneriler").text
