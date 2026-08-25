"""src/services/chat_flow.py için testler (bkz. plan "Web Chatbox" Faz 2/3) —
saf durum makinesi, HTTP/route yok. Gerçek LLM YOK: `_ScriptedLLMProvider`
sistem promptunun ayırt edici bir alt-dizesine göre kayıtlı JSON döndürür
(bir turda birden fazla LLM çağrısı olabileceğinden — niyet sınıflandırma +
çıkarım — pozisyonel bir kuyruktan daha sağlam). Gerçek Google Calendar YOK:
`FakeCalendar` çağrıları kaydeden basit bir sahte.

`save_candidate`/`update_candidate_status`/`record_audit` gerçek `conn`
(temp_db) üzerinde çalışıyor — `candidate_events`'in hiçbir FK'si yok (yalnızca
`candidate_sources` FK gerektirir, o da `save_candidate` tarafından hiç
kullanılmıyor), bu yüzden hesap/mail kurulumuna gerek yok."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.core.models import CandidateStatus
from src.policies.store import get_active_policies
from src.providers.base import EmbeddingProvider, LLMProvider
from src.services.chat_flow import (
    ACM_KIND_EDIT,
    ACM_KIND_REJECT,
    CONFLICT_CANCELLED,
    CONFLICT_KEPT_ANYWAY,
    CONFLICT_MOVED,
    CONFLICT_UNRESOLVED,
    FLOW_CREATE_EVENT,
    ChatState,
    STEP_ACM_ASK_APPLY_FUTURE,
    STEP_ACM_ASK_REJECT_FEEDBACK,
    STEP_ACM_ASK_SCOPE,
    STEP_ASK_CONFLICT_ALTERNATIVE,
    STEP_ASK_DURATION,
    STEP_ASK_TITLE,
    STEP_EDIT_PICK_FIELD,
    STEP_PREVIEW_CONFIRM,
    STEP_UPDATE_CONFIRM_DELETE,
    STEP_UPDATE_CONFIRM_MOVE,
    STEP_UPDATE_DISAMBIGUATE,
    advance,
)
from src.storage.db import get_connection

TZ = timezone.utc


class _ScriptedLLMProvider(LLMProvider):
    """`(sistem_promptu_alt_dizesi, yanıt_dict)` çiftleri — ilk eşleşen
    kullanılır. Bir tur birden fazla LLM çağrısı yapabildiğinden (niyet
    sınıflandırma + çıkarım) pozisyonel bir kuyruktan daha sağlam."""

    def __init__(self, responses: list[tuple[str, dict]]):
        self._responses = responses

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        for needle, response in self._responses:
            if needle in system_prompt:
                return json.dumps(response)
        raise AssertionError(f"Beklenmeyen sistem promptu: {system_prompt[:120]!r}")

    def is_available(self):
        return True


class _DummyEmbeddingProvider(EmbeddingProvider):
    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    @property
    def model_name(self):
        return "dummy-embedding"

    @property
    def dimension(self):
        return 8


class FakeCalendar:
    def __init__(self, busy: list[tuple[datetime, datetime]] | None = None, events: list[dict] | None = None):
        self._busy = busy or []
        self._events = events or []
        self.created_events: list[dict] = []
        self.updated_events: list[tuple[str, dict]] = []
        self.deleted_event_ids: list[str] = []

    def list_events(self, time_min, time_max, calendar_id="primary"):
        return self._events

    def get_freebusy(self, time_min, time_max, calendar_id="primary"):
        return self._busy

    def create_event(self, *, title, start, end, location=None, calendar_id="primary"):
        # Google'ın eski JSON şeklini burada YENİDEN kuruyoruz — connector
        # arayüzü artık düz alan (title/start/end) alıyor (bkz. CalendarConnector,
        # normalizasyon sızıntısı düzeltmesi), ama bu testlerin assertion'ları
        # zaten bu şekle göre yazılmış; sahte, kaydettiği veriyi bu şekilde
        # tutmaya devam ediyor ki testler değişmesin.
        event = {"summary": title, "location": location, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        self.created_events.append(event)
        return f"evt-{len(self.created_events)}"

    def update_event(self, event_id, *, title=None, start=None, end=None, location=None, calendar_id="primary"):
        changes: dict = {}
        if title is not None:
            changes["summary"] = title
        if location is not None:
            changes["location"] = location
        if start is not None:
            changes["start"] = {"dateTime": start.isoformat()}
        if end is not None:
            changes["end"] = {"dateTime": end.isoformat()}
        self.updated_events.append((event_id, changes))

    def delete_event(self, event_id, calendar_id="primary"):
        self.deleted_event_ids.append(event_id)


def _intent_llm(intent: str, **kwargs) -> _ScriptedLLMProvider:
    response = {"intent": intent, "query_range_start": None, "query_range_end": None}
    response.update(kwargs)
    return _ScriptedLLMProvider([("niyetini sınıflandır", response)])


def _create_event_llm(intent_extra=None, **fields) -> _ScriptedLLMProvider:
    extraction_fields = {
        "event_type": "appointment",
        "title": None,
        "start_datetime": None,
        "duration_minutes": None,
        "location": None,
        "ambiguous_fields": [],
    }
    extraction_fields.update(fields)
    intent_response = {"intent": "create_event", "query_range_start": None, "query_range_end": None}
    if intent_extra:
        intent_response.update(intent_extra)
    return _ScriptedLLMProvider(
        [
            ("niyetini sınıflandır", intent_response),
            ("bir etkinlik bilgisi çıkar", extraction_fields),
        ]
    )


def _create_event_llm_multi(events: list[dict], intent_extra=None) -> _ScriptedLLMProvider:
    """`_create_event_llm`'in ÇOKLU-etkinlik karşılığı — model bir JSON
    LİSTESİ döndürdüğünde (bkz. extract_candidate_events_from_text)."""
    intent_response = {"intent": "create_event", "query_range_start": None, "query_range_end": None}
    if intent_extra:
        intent_response.update(intent_extra)
    return _ScriptedLLMProvider(
        [
            ("niyetini sınıflandır", intent_response),
            ("bir etkinlik bilgisi çıkar", events),
        ]
    )


def _update_event_llm(**fields) -> _ScriptedLLMProvider:
    update_fields = {
        "title_hint": None,
        "date_hint": None,
        "cancel": False,
        "new_start_datetime": None,
        "new_duration_minutes": None,
    }
    update_fields.update(fields)
    intent_response = {"intent": "update_event", "query_range_start": None, "query_range_end": None}
    return _ScriptedLLMProvider(
        [
            ("niyetini sınıflandır", intent_response),
            ("VAR OLAN bir etkinliği", update_fields),
        ]
    )


def _google_event(event_id: str, summary: str, start: datetime, end: datetime) -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }


def _future_dt(**kwargs) -> datetime:
    return (datetime.now(TZ) + timedelta(**kwargs)).replace(microsecond=0)


@pytest.fixture
def conn(temp_db):
    with get_connection() as c:
        yield c


def _advance(state, text, *, llm, embedding_provider=None, calendar=None, lang="tr"):
    return advance(
        state, text,
        llm=llm,
        embedding_provider=embedding_provider or _DummyEmbeddingProvider(),
        calendar=calendar or FakeCalendar(),
        lang=lang,
    )


# --- Mutlu yol: eksik alan yok, çakışma yok ---


def test_create_event_happy_path_no_missing_fields(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Diş hekimi", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()

    state, messages = _advance(ChatState(), "yarın diş hekimine gidiyorum", llm=llm, calendar=calendar)

    assert state.step == STEP_PREVIEW_CONFIRM
    assert any("Diş hekimi" in m for m in messages)

    state, messages = _advance(state, "approve", llm=llm, calendar=calendar)
    assert state.flow is None  # akış bitti
    assert len(calendar.created_events) == 1
    assert calendar.created_events[0]["summary"] == "Diş hekimi"


# --- Serbest metinde çoklu etkinlik (bkz. canlı testte bulunan sorun: model
# ya iki etkinliği karıştırıp saçma bir candidate üretiyordu, ya da bir JSON
# LİSTESİ döndürüp eski tek-nesne bekleyen kod AttributeError ile çöküyordu,
# chat_routes.py'de sessizce yutuluyordu) ---


def test_create_event_two_events_in_one_message_queues_second(conn):
    start1 = _future_dt(days=1, hours=2)
    start2 = _future_dt(days=2, hours=4)
    llm = _create_event_llm_multi([
        {"event_type": "meeting", "title": "Toplantı A", "start_datetime": start1.isoformat(),
         "duration_minutes": 30, "location": None, "ambiguous_fields": []},
        {"event_type": "meeting", "title": "Toplantı B", "start_datetime": start2.isoformat(),
         "duration_minutes": 45, "location": None, "ambiguous_fields": []},
    ])
    calendar = FakeCalendar()

    state, messages = _advance(
        ChatState(), "toplantı A yarın, toplantı B da öbür gün", llm=llm, calendar=calendar
    )

    assert state.step == STEP_PREVIEW_CONFIRM
    assert len(state.queued_candidates) == 1
    assert state.queued_candidates[0].title == "Toplantı B"
    assert state.batch_total == 2
    assert state.batch_index == 1
    assert any("Toplantı A" in m for m in messages)
    assert any("1/2" in m for m in messages)  # batch_progress mesajı ("Etkinlik 1/2:")

    # İlkini onaylayınca ikincisi otomatik başlamalı (_finish_candidate).
    state, messages = _advance(state, "approve", llm=llm, calendar=calendar)
    assert len(calendar.created_events) == 1
    assert calendar.created_events[0]["summary"] == "Toplantı A"
    assert state.flow == FLOW_CREATE_EVENT  # ikinci aday için akış devam ediyor
    assert state.step == STEP_PREVIEW_CONFIRM
    assert state.candidate.title == "Toplantı B"
    assert state.queued_candidates == []

    state, messages = _advance(state, "approve", llm=llm, calendar=calendar)
    assert state.flow is None  # ikinci de bitince akış tamamen kapanıyor
    assert len(calendar.created_events) == 2
    assert calendar.created_events[1]["summary"] == "Toplantı B"


def test_create_event_single_dict_response_still_works_without_list(conn):
    # generate_json'ın modelden tek bir çıplak nesne (liste değil) aldığı
    # eski/çoğunluk durum hâlâ birebir aynı davranmalı (geriye dönük uyumluluk).
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Diş hekimi", start_datetime=start.isoformat(), duration_minutes=30)

    state, _ = _advance(ChatState(), "yarın diş hekimine gidiyorum", llm=llm)

    assert state.queued_candidates == []
    assert state.batch_total == 0
    assert state.candidate.title == "Diş hekimi"


def test_multi_day_event_preview_shows_end_date_not_just_time(conn):
    # Regresyon: "5 gün süren bir tatil" gibi çok günlü bir etkinlik önizlemede
    # "31 Ağustos 2026, 08:00 – 08:00" gibi anlamsız/sıfır-süreli görünüyordu
    # (canlı testte bulundu) — süre doğruydu (7200 dk), yalnızca önizleme
    # bitiş SAATİNİ gösterip TARİHİNİ düşürüyordu.
    start = (_future_dt(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)
    llm = _create_event_llm(
        event_type="travel", title="Tatil", start_datetime=start.isoformat(), duration_minutes=5 * 24 * 60
    )
    calendar = FakeCalendar()

    state, messages = _advance(ChatState(), "haftaya pazartesiden 5 gün süren bir tatil", llm=llm, calendar=calendar)

    assert state.step == STEP_PREVIEW_CONFIRM
    end = start + timedelta(minutes=5 * 24 * 60)
    preview = "\n".join(messages)
    assert "08:00 – 08:00" not in preview  # eski hata: bitiş tarihi düşüp saatler çakışıyormuş gibi görünüyordu
    assert str(end.year) in preview and "08:00" in preview  # bitiş tarihi gerçekten gösteriliyor


def test_create_event_reject_does_not_write_calendar(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Diş hekimi", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()

    state, _ = _advance(ChatState(), "yarın diş hekimine gidiyorum", llm=llm, calendar=calendar)
    state, messages = _advance(state, "reject", llm=llm, calendar=calendar)

    # Reddedilince akış bitmiyor — ACM "neden reddettiniz?" soruyor (bkz. plan).
    assert state.flow == FLOW_CREATE_EVENT
    assert state.step == STEP_ACM_ASK_REJECT_FEEDBACK
    assert calendar.created_events == []
    assert any("Reddedildi" in m or "Rejected" in m for m in messages)


# --- ACM: "gelecekte de uygulayayım mı?" (reddetme sonrası, bkz. plan) ---


def test_acm_reject_skip_ends_flow_without_saving_correction(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "reject", llm=llm, calendar=calendar)
    assert state.step == STEP_ACM_ASK_REJECT_FEEDBACK

    state, messages = _advance(state, "atla", llm=llm, calendar=calendar)
    assert state.flow is None
    assert messages

    count = conn.execute("SELECT COUNT(*) FROM user_corrections").fetchone()[0]
    assert count == 0


def test_acm_reject_empty_feedback_also_skips(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "reject", llm=llm, calendar=calendar)
    state, _ = _advance(state, "   ", llm=llm, calendar=calendar)
    assert state.flow is None
    count = conn.execute("SELECT COUNT(*) FROM user_corrections").fetchone()[0]
    assert count == 0


def test_acm_reject_feedback_declined_saves_raw_correction_only(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "reject", llm=llm, calendar=calendar)
    state, _ = _advance(state, "yanlış saatti", llm=llm, calendar=calendar)
    assert state.step == STEP_ACM_ASK_APPLY_FUTURE

    state, _ = _advance(state, "hayır", llm=llm, calendar=calendar)
    assert state.flow is None

    row = conn.execute("SELECT approved_for_future_use, derived_policy_id FROM user_corrections").fetchone()
    assert row["approved_for_future_use"] == 0
    assert row["derived_policy_id"] is None
    assert get_active_policies() == []


def test_acm_reject_feedback_apply_future_saves_policy(conn):
    start = _future_dt(days=1, hours=2)
    llm = _ScriptedLLMProvider(
        [
            ("niyetini sınıflandır", {"intent": "create_event", "query_range_start": None, "query_range_end": None}),
            (
                "bir etkinlik bilgisi çıkar",
                {
                    "event_type": "meeting", "title": "Toplantı", "start_datetime": start.isoformat(),
                    "duration_minutes": 30, "location": None, "ambiguous_fields": [],
                },
            ),
            (
                "kişisel bir kural",
                {"event_type": None, "default_duration_minutes": 15, "reminder_minutes_before": None, "importance": None},
            ),
        ]
    )
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "reject", llm=llm, calendar=calendar)
    state, _ = _advance(state, "süre 15 dk olmalıydı", llm=llm, calendar=calendar)
    assert state.step == STEP_ACM_ASK_APPLY_FUTURE
    assert state.acm_kind == ACM_KIND_REJECT

    state, _ = _advance(state, "evet", llm=llm, calendar=calendar)
    assert state.step == STEP_ACM_ASK_SCOPE

    state, messages = _advance(state, "2", llm=llm, calendar=calendar)  # her zaman
    assert state.flow is None
    assert messages

    policies = get_active_policies()
    assert len(policies) == 1
    assert policies[0].structured_action.get("default_duration_minutes") == 15
    assert policies[0].structured_conditions.get("event_type") is None  # "her zaman" -> global kapsam

    row = conn.execute(
        "SELECT approved_for_future_use, derived_policy_id FROM user_corrections"
    ).fetchone()
    assert row["approved_for_future_use"] == 1
    assert row["derived_policy_id"] == policies[0].policy_id


# --- ACM: düzenleme sonrası onay (bkz. plan) ---


def test_acm_edit_then_approve_apply_future_saves_policy(conn):
    """capture_edit_correction'ın CLI'daki karşılığı: hangi alanın ne olması
    gerektiği zaten kesin bilindiği için (kullanıcı direkt yazdı) scope
    seçimi sırasında hiç LLM çağrısı yapılmıyor — _create_event_llm'in
    yalnızca intent+extraction script'i olması bunu doğrular (üçüncü bir
    çağrı gelseydi _ScriptedLLMProvider AssertionError fırlatırdı)."""
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(event_type="meeting", title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "edit", llm=llm, calendar=calendar)
    state, _ = _advance(state, "süre", llm=llm, calendar=calendar)
    state, _ = _advance(state, "90 dakika", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM

    state, _ = _advance(state, "approve", llm=llm, calendar=calendar)
    assert len(calendar.created_events) == 1
    assert state.step == STEP_ACM_ASK_APPLY_FUTURE
    assert state.acm_kind == ACM_KIND_EDIT

    state, _ = _advance(state, "evet", llm=llm, calendar=calendar)
    assert state.step == STEP_ACM_ASK_SCOPE

    state, messages = _advance(state, "1", llm=llm, calendar=calendar)  # yalnızca bu tür
    assert state.flow is None
    assert messages

    policies = get_active_policies()
    assert len(policies) == 1
    assert policies[0].structured_action.get("default_duration_minutes") == 90
    assert policies[0].structured_conditions.get("event_type") == "meeting"


# --- Eksik alan netleştirme ---


def test_create_event_asks_for_missing_title(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title=None, start_datetime=start.isoformat(), duration_minutes=30)

    state, messages = _advance(ChatState(), "yarın 14'te randevum var", llm=llm)

    assert state.step == STEP_ASK_TITLE
    assert any("başlığı" in m.lower() or "called" in m.lower() for m in messages)

    state, messages = _advance(state, "Diş hekimi", llm=llm)
    assert state.candidate.title == "Diş hekimi"
    assert state.step == STEP_PREVIEW_CONFIRM


def test_create_event_empty_title_reprompts(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title=None, start_datetime=start.isoformat(), duration_minutes=30)

    state, _ = _advance(ChatState(), "bir şey", llm=llm)
    assert state.step == STEP_ASK_TITLE

    state, _ = _advance(state, "   ", llm=llm)
    assert state.step == STEP_ASK_TITLE  # boş başlık kabul edilmez, tekrar sorulur


def test_create_event_duration_default_for_meeting(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(
        event_type="meeting", title="Ekip toplantısı", start_datetime=start.isoformat(), duration_minutes=None
    )

    state, messages = _advance(ChatState(), "yarın ekip toplantısı var", llm=llm)

    assert state.candidate.duration_minutes == 60  # DEFAULT_MEETING_DURATION_MINUTES
    assert any("60" in m for m in messages)
    assert state.step == STEP_PREVIEW_CONFIRM


def test_create_event_duration_bounded_retry_exhausts(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(
        event_type="appointment", title="Randevu", start_datetime=start.isoformat(), duration_minutes=None
    )

    state, _ = _advance(ChatState(), "yarın randevum var", llm=llm)
    assert state.step == STEP_ASK_DURATION

    state, _ = _advance(state, "anlamsız", llm=llm)
    assert state.step == STEP_ASK_DURATION
    assert state.attempts == 1

    state, _ = _advance(state, "yine anlamsız", llm=llm)
    assert state.attempts == 2

    state, messages = _advance(state, "hâlâ olmuyor", llm=llm)
    assert state.flow is None  # 3. denemede vazgeçildi
    assert any("baştan" in m.lower() or "start over" in m.lower() for m in messages)


def test_create_event_duration_valid_after_retry(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(
        event_type="appointment", title="Randevu", start_datetime=start.isoformat(), duration_minutes=None
    )
    state, _ = _advance(ChatState(), "yarın randevum var", llm=llm)
    state, _ = _advance(state, "olmadı", llm=llm)
    state, _ = _advance(state, "45 dakika", llm=llm)
    assert state.candidate.duration_minutes == 45
    assert state.step == STEP_PREVIEW_CONFIRM


def test_create_event_ambiguous_time_resolved(conn):
    start_date_only = _future_dt(days=1).replace(hour=0, minute=0, second=0)
    llm = _create_event_llm(
        title="Öğle yemeği", start_datetime=start_date_only.isoformat(), duration_minutes=60,
        ambiguous_fields=["start_datetime"],
    )
    state, messages = _advance(ChatState(), "yarın öğleden sonra yemek", llm=llm)
    assert "ask_ambiguous_time" in (state.step or "") or state.step is not None
    state, _ = _advance(state, "13:00", llm=llm)
    assert state.candidate.start_datetime.hour == 13
    assert state.step == STEP_PREVIEW_CONFIRM


# --- Çakışma çözümü ---


def test_conflict_with_alternative_chosen_moves_event(conn):
    start = _future_dt(days=1, hours=2)
    busy = [(start, start + timedelta(minutes=30))]
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar(busy=busy)

    state, messages = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    assert state.step == STEP_ASK_CONFLICT_ALTERNATIVE
    assert state.alternatives

    state, messages = _advance(state, "1", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM
    assert state.conflict_note == CONFLICT_MOVED
    assert state.candidate.start_datetime == state.alternatives[0]


def test_conflict_keep_anyway(conn):
    start = _future_dt(days=1, hours=2)
    busy = [(start, start + timedelta(minutes=30))]
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar(busy=busy)

    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "d", llm=llm, calendar=calendar)
    assert state.conflict_note == CONFLICT_KEPT_ANYWAY
    assert state.step == STEP_PREVIEW_CONFIRM


def test_conflict_invalid_choice_on_initial_resolution_auto_rejects(conn):
    """İLK çakışma çözümlemesinde ne numara ne 'd' -> CLI'daki gibi
    önizlemeye hiç girmeden otomatik reddedilir (bkz. resolve_conflicts_interactively
    'Var (iptal edilecek)' + review_and_confirm_candidate satır 629)."""
    start = _future_dt(days=1, hours=2)
    busy = [(start, start + timedelta(minutes=30))]
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar(busy=busy)

    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    assert state.step == STEP_ASK_CONFLICT_ALTERNATIVE

    state, messages = _advance(state, "saçma bir cevap", llm=llm, calendar=calendar)

    assert state.flow is None
    assert calendar.created_events == []
    # _reject_at_conflict kendi kısa bağlantısında yazıp commit ediyor (bkz.
    # chat_flow.py) — burada AYRI bir bağlantıyla sorgulamak da güvenli.
    row = conn.execute("SELECT status FROM candidate_events").fetchone()
    assert row["status"] == CandidateStatus.REJECTED.value


def test_conflict_no_alternatives_declined_is_not_auto_rejected(conn):
    """Alternatif YOK + kullanıcı 'hayır' derse CLI 'Var (çözülmedi)' döner —
    bu OTOMATİK REDDETMEZ, önizlemeye devam eder (bkz. plan: bu iki durum
    'iptal edilecek' ile karıştırılmamalı)."""
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)

    class _NoAlternativesCalendar(FakeCalendar):
        def get_freebusy(self, time_min, time_max, calendar_id="primary"):
            # find_conflicts çağrısı çakışma bulsun, ama suggest_alternative_slots
            # çağrısı (aynı metottan) hiç boş slot bulamasın: tüm pencereyi
            # meşgul göster.
            return [(time_min, time_max)]

    calendar = _NoAlternativesCalendar()
    state, messages = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    assert state.step == "ask_conflict_no_alternatives"

    state, messages = _advance(state, "hayır", llm=llm, calendar=calendar)

    assert state.step == STEP_PREVIEW_CONFIRM  # reddedilmedi, önizlemeye geçti
    assert state.conflict_note == CONFLICT_UNRESOLVED


# --- Çok-turlu düzenleme döngüsü ---


def test_multi_round_edit_loop_updates_final_calendar_event(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()

    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM

    # 1. düzenleme turu: süreyi değiştir
    state, _ = _advance(state, "edit", llm=llm, calendar=calendar)
    state, _ = _advance(state, "süre", llm=llm, calendar=calendar)
    state, _ = _advance(state, "90 dakika", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM
    assert state.candidate.duration_minutes == 90
    assert state.edited_structured_action.get("default_duration_minutes") == 90

    # 2. düzenleme turu: başlığı değiştir
    state, _ = _advance(state, "edit", llm=llm, calendar=calendar)
    state, _ = _advance(state, "başlık", llm=llm, calendar=calendar)
    state, _ = _advance(state, "Ekip Toplantısı", llm=llm, calendar=calendar)
    assert state.candidate.title == "Ekip Toplantısı"
    assert state.step == STEP_PREVIEW_CONFIRM

    state, _ = _advance(state, "approve", llm=llm, calendar=calendar)
    assert len(calendar.created_events) == 1
    assert calendar.created_events[0]["summary"] == "Ekip Toplantısı"
    end_dt = start + timedelta(minutes=90)
    assert calendar.created_events[0]["end"]["dateTime"] == end_dt.isoformat()

    # save_candidate yalnızca BİR kez çağrılmış olmalı (ilk preview girişinde)
    count = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
    assert count == 1


def test_edit_then_new_conflict_goes_through_conflict_subflow(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()  # başlangıçta çakışma yok

    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM

    # Düzenleme sonrası çakışma ortaya çıksın diye takvimi güncelliyoruz.
    calendar._busy = [(start, start + timedelta(minutes=30))]

    state, _ = _advance(state, "edit", llm=llm, calendar=calendar)
    state, messages = _advance(state, "süre", llm=llm, calendar=calendar)
    state, messages = _advance(state, "30 dakika", llm=llm, calendar=calendar)

    assert state.step == STEP_ASK_CONFLICT_ALTERNATIVE

    # Bu kez "iptal edilecek" bir cevap versek bile OTOMATİK reddedilmemeli
    # (candidate zaten kaydedilmişti) — yalnızca not olarak önizlemeye döner.
    state, messages = _advance(state, "hiçbiri", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM
    assert state.conflict_note == CONFLICT_CANCELLED


# --- query_calendar ---


def test_query_calendar_no_range_asks_for_clarification(conn):
    llm = _intent_llm("query_calendar")
    state, messages = _advance(ChatState(), "takvimimde ne var", llm=llm)
    assert state.flow is None
    assert messages


def test_query_calendar_with_events(conn):
    start = _future_dt(days=1)
    end = start + timedelta(days=1)
    llm = _intent_llm("query_calendar", query_range_start=start.isoformat(), query_range_end=end.isoformat())

    class _CalendarWithEvents(FakeCalendar):
        def list_events(self, time_min, time_max, calendar_id="primary"):
            return [
                {
                    "id": "e1",
                    "summary": "Diş hekimi",
                    "status": "confirmed",
                    "start": {"dateTime": (start + timedelta(hours=10)).isoformat()},
                    "end": {"dateTime": (start + timedelta(hours=11)).isoformat()},
                }
            ]

    state, messages = _advance(ChatState(), "yarın ne var", llm=llm, calendar=_CalendarWithEvents())
    assert state.flow is None
    assert any("Diş hekimi" in m for m in messages)


def test_query_calendar_no_events(conn):
    start = _future_dt(days=1)
    end = start + timedelta(days=1)
    llm = _intent_llm("query_calendar", query_range_start=start.isoformat(), query_range_end=end.isoformat())
    state, messages = _advance(ChatState(), "yarın ne var", llm=llm)
    assert state.flow is None
    assert messages


# --- Diğer niyetler ---


def test_update_event_intent_returns_not_available_message(conn):
    llm = _intent_llm("update_event")
    state, messages = _advance(ChatState(), "toplantıyı taşı", llm=llm)
    assert state.flow is None
    assert messages


def _define_policy_llm(**fields) -> _ScriptedLLMProvider:
    extraction_fields = {
        "event_type": None, "default_duration_minutes": None,
        "reminder_minutes_before": None, "importance": None,
    }
    extraction_fields.update(fields)
    intent_response = {"intent": "define_policy", "query_range_start": None, "query_range_end": None}
    return _ScriptedLLMProvider(
        [
            ("niyetini sınıflandır", intent_response),
            ("kişisel bir kural", extraction_fields),
        ]
    )


def test_define_policy_saves_active_policy(conn):
    llm = _define_policy_llm(default_duration_minutes=45)
    state, messages = _advance(ChatState(), "toplantılar her zaman 45 dakika olsun", llm=llm)
    assert state.flow is None
    assert messages

    policies = get_active_policies()
    assert len(policies) == 1
    assert policies[0].structured_action.get("default_duration_minutes") == 45


def test_define_policy_no_rule_extracted_saves_nothing(conn):
    llm = _define_policy_llm()  # tüm alanlar None -> somut bir davranış yok
    state, messages = _advance(ChatState(), "bir şey", llm=llm)
    assert state.flow is None
    assert messages
    assert get_active_policies() == []


def test_other_intent_returns_fallback_message(conn):
    llm = _intent_llm("other")
    state, messages = _advance(ChatState(), "merhaba nasılsın", llm=llm)
    assert state.flow is None
    assert messages


# --- İngilizce dil desteği (aynı akış, farklı lang) ---


def test_create_event_english_messages(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Dentist", start_datetime=start.isoformat(), duration_minutes=30)
    state, messages = _advance(ChatState(), "dentist tomorrow", llm=llm, lang="en")
    assert state.step == STEP_PREVIEW_CONFIRM
    assert any("Dentist" in m for m in messages)


# --- update_event (Faz 3) ---


def test_update_event_no_matches_gives_up(conn):
    llm = _update_event_llm(title_hint="olmayan toplantı")
    calendar = FakeCalendar(events=[])

    state, messages = _advance(ChatState(), "olmayan toplantıyı sil", llm=llm, calendar=calendar)

    assert state.flow is None
    assert messages


def test_update_event_single_match_delete_confirm_flow(conn):
    start = _future_dt(days=1, hours=2)
    event = _google_event("evt-1", "Proje Toplantısı", start, start + timedelta(minutes=30))
    llm = _update_event_llm(title_hint="proje", cancel=True)
    calendar = FakeCalendar(events=[event])

    state, messages = _advance(ChatState(), "proje toplantısını iptal et", llm=llm, calendar=calendar)
    assert state.step == STEP_UPDATE_CONFIRM_DELETE
    assert any("Proje Toplantısı" in m for m in messages)

    state, messages = _advance(state, "evet", llm=llm, calendar=calendar)
    assert state.flow is None
    assert calendar.deleted_event_ids == ["evt-1"]


def test_update_event_delete_confirm_declined_does_not_delete(conn):
    start = _future_dt(days=1, hours=2)
    event = _google_event("evt-1", "Proje Toplantısı", start, start + timedelta(minutes=30))
    llm = _update_event_llm(title_hint="proje", cancel=True)
    calendar = FakeCalendar(events=[event])

    state, _ = _advance(ChatState(), "proje toplantısını iptal et", llm=llm, calendar=calendar)
    state, messages = _advance(state, "hayır", llm=llm, calendar=calendar)

    assert state.flow is None
    assert calendar.deleted_event_ids == []


def test_update_event_multiple_matches_disambiguation(conn):
    start = _future_dt(days=1, hours=2)
    event1 = _google_event("evt-1", "Proje Toplantısı A", start, start + timedelta(minutes=30))
    event2 = _google_event("evt-2", "Proje Toplantısı B", start + timedelta(hours=3), start + timedelta(hours=4))
    llm = _update_event_llm(title_hint="proje", new_start_datetime=(start + timedelta(hours=5)).isoformat())
    calendar = FakeCalendar(events=[event1, event2])

    state, messages = _advance(ChatState(), "proje toplantısını taşı", llm=llm, calendar=calendar)
    assert state.step == STEP_UPDATE_DISAMBIGUATE
    assert any("Proje Toplantısı A" in m for m in messages)
    assert any("Proje Toplantısı B" in m for m in messages)

    state, messages = _advance(state, "2", llm=llm, calendar=calendar)
    assert state.step == STEP_UPDATE_CONFIRM_MOVE
    assert state.matched_event["id"] == "evt-2"


def test_update_event_disambiguation_invalid_choice_cancels(conn):
    start = _future_dt(days=1, hours=2)
    event1 = _google_event("evt-1", "A", start, start + timedelta(minutes=30))
    event2 = _google_event("evt-2", "B", start + timedelta(hours=3), start + timedelta(hours=4))
    llm = _update_event_llm(title_hint="toplantı")
    calendar = FakeCalendar(events=[event1, event2])

    state, _ = _advance(ChatState(), "toplantıyı taşı", llm=llm, calendar=calendar)
    assert state.step == STEP_UPDATE_DISAMBIGUATE

    state, messages = _advance(state, "hiçbiri", llm=llm, calendar=calendar)
    assert state.flow is None  # CLI'daki gibi retry yok, direkt iptal
    assert messages


def test_update_event_move_without_conflict(conn):
    start = _future_dt(days=1, hours=2)
    new_start = start + timedelta(days=1)
    event = _google_event("evt-1", "Proje Toplantısı", start, start + timedelta(minutes=30))
    llm = _update_event_llm(title_hint="proje", new_start_datetime=new_start.isoformat())
    calendar = FakeCalendar(events=[event])

    state, messages = _advance(ChatState(), "proje toplantısını yarına taşı", llm=llm, calendar=calendar)
    assert state.step == STEP_UPDATE_CONFIRM_MOVE
    assert not any("⚠" in m for m in messages)

    state, messages = _advance(state, "evet", llm=llm, calendar=calendar)
    assert state.flow is None
    assert len(calendar.updated_events) == 1
    event_id, changes = calendar.updated_events[0]
    assert event_id == "evt-1"
    assert changes["start"]["dateTime"] == new_start.isoformat()
    # Süre belirtilmediği için eski etkinliğin süresi (30 dk) korunmalı.
    assert changes["end"]["dateTime"] == (new_start + timedelta(minutes=30)).isoformat()


def test_update_event_move_with_conflict_shows_warning(conn):
    start = _future_dt(days=1, hours=2)
    new_start = start + timedelta(days=1)
    event = _google_event("evt-1", "Proje Toplantısı", start, start + timedelta(minutes=30))
    llm = _update_event_llm(title_hint="proje", new_start_datetime=new_start.isoformat())

    class _ConflictingCalendar(FakeCalendar):
        def get_freebusy(self, time_min, time_max, calendar_id="primary"):
            return [(new_start, new_start + timedelta(minutes=30))]

    calendar = _ConflictingCalendar(events=[event])
    state, messages = _advance(ChatState(), "proje toplantısını yarına taşı", llm=llm, calendar=calendar)

    assert state.step == STEP_UPDATE_CONFIRM_MOVE
    assert any("⚠" in m for m in messages)


def test_update_event_move_no_new_time_gives_up(conn):
    start = _future_dt(days=1, hours=2)
    event = _google_event("evt-1", "Proje Toplantısı", start, start + timedelta(minutes=30))
    llm = _update_event_llm(title_hint="proje")  # new_start_datetime yok
    calendar = FakeCalendar(events=[event])

    state, messages = _advance(ChatState(), "proje toplantısını değiştir", llm=llm, calendar=calendar)
    assert state.flow is None
    assert messages


def test_update_event_move_uses_explicit_duration(conn):
    start = _future_dt(days=1, hours=2)
    new_start = start + timedelta(days=1)
    event = _google_event("evt-1", "Proje Toplantısı", start, start + timedelta(minutes=30))
    llm = _update_event_llm(title_hint="proje", new_start_datetime=new_start.isoformat(), new_duration_minutes=90)
    calendar = FakeCalendar(events=[event])

    state, _ = _advance(ChatState(), "proje toplantısını taşı", llm=llm, calendar=calendar)
    state, _ = _advance(state, "evet", llm=llm, calendar=calendar)

    _, changes = calendar.updated_events[0]
    assert changes["end"]["dateTime"] == (new_start + timedelta(minutes=90)).isoformat()


# --- preview_confirm serbest metin eş anlamlıları (CLI'nın [e/h/d] kısayolu) ---


@pytest.mark.parametrize("word", ["e", "evet", "onayla", "approve"])
def test_preview_confirm_accepts_approve_synonyms(conn, word):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, word, llm=llm, calendar=calendar)
    assert len(calendar.created_events) == 1


@pytest.mark.parametrize("word", ["h", "hayır", "reddet", "reject"])
def test_preview_confirm_accepts_reject_synonyms(conn, word):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, word, llm=llm, calendar=calendar)
    assert calendar.created_events == []
    assert state.step == STEP_ACM_ASK_REJECT_FEEDBACK  # ACM takip sorusu (bkz. plan)


@pytest.mark.parametrize("word", ["d", "düzenle", "edit"])
def test_preview_confirm_accepts_edit_synonyms(conn, word):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, _ = _advance(state, word, llm=llm, calendar=calendar)
    assert state.step == STEP_EDIT_PICK_FIELD


def test_preview_confirm_unrecognized_input_does_not_reject(conn):
    start = _future_dt(days=1, hours=2)
    llm = _create_event_llm(title="Toplantı", start_datetime=start.isoformat(), duration_minutes=30)
    calendar = FakeCalendar()
    state, _ = _advance(ChatState(), "yarın toplantı", llm=llm, calendar=calendar)
    state, messages = _advance(state, "bunu anlamıyorum", llm=llm, calendar=calendar)
    assert state.step == STEP_PREVIEW_CONFIRM  # önizlemede kalır, sessizce reddetmez
    # Canlı testte bulundu: kullanıcı neden hiçbir şeyin değişmediğini
    # anlayamıyordu (mesaj sessizce yok sayılıyordu) — artık açıklayan bir
    # mesaj da dönüyor, yalnızca önizleme tekrarlanmıyor.
    assert len(messages) == 2
    assert any("anlayamadım" in m.lower() or "understand" in m.lower() for m in messages)
