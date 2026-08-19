"""src/ui/chat_state.py için testler (bkz. plan "Web Chatbox" Faz 4) —
kalıcılık katmanı: ChatState <-> chat_sessions.state_json, chat_messages
yazma/okuma, process_message'ın tek transaction'da çalışması."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from src.connectors.account_registry import ensure_account_registered
from src.providers.base import EmbeddingProvider, LLMProvider
from src.services.chat_flow import ChatState
from src.storage.db import get_connection
from src.ui.chat_state import (
    MESSAGE_HISTORY_LIMIT,
    append_chat_message,
    list_recent_messages,
    load_chat_state,
    process_message,
    save_chat_state,
)


class _OtherIntentLLM(LLMProvider):
    """Her zaman 'other' niyeti döner — tek turlu, state değişmeyen bir akış
    (kalıcılık katmanını test etmek için en basit senaryo)."""

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return json.dumps({"intent": "other", "query_range_start": None, "query_range_end": None})

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


class _NullCalendar:
    def list_events(self, time_min, time_max, calendar_id="primary"):
        return []

    def get_freebusy(self, time_min, time_max, calendar_id="primary"):
        return []

    def create_event(self, event, calendar_id="primary"):
        return "evt-1"

    def update_event(self, event_id, changes, calendar_id="primary"):
        pass

    def delete_event(self, event_id, calendar_id="primary"):
        pass


def _new_session(account_id: str = "acc1") -> str:
    ensure_account_registered(account_id, provider="google", email=f"{account_id}@example.com")
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, account_id, flow, step, state_json, created_at, updated_at) "
            "VALUES (?,?,NULL,NULL,'{}',?,?)",
            (session_id, account_id, now, now),
        )
    return session_id


@pytest.fixture
def kwargs():
    return dict(llm=_OtherIntentLLM(), embedding_provider=_DummyEmbeddingProvider(), calendar=_NullCalendar(), lang="tr")


def test_load_chat_state_unknown_session_returns_default(temp_db):
    assert load_chat_state("olmayan-id") == ChatState()


def test_save_and_load_chat_state_round_trips(temp_db):
    session_id = _new_session()
    state = ChatState(flow="create_event", step="ask_title")
    with get_connection() as conn:
        save_chat_state(conn, session_id, state)

    loaded = load_chat_state(session_id)
    assert loaded.flow == "create_event"
    assert loaded.step == "ask_title"


def test_append_and_list_recent_messages_chronological(temp_db):
    session_id = _new_session()
    with get_connection() as conn:
        append_chat_message(conn, session_id, "user", "birinci")
        append_chat_message(conn, session_id, "assistant", "ikinci")
        append_chat_message(conn, session_id, "user", "üçüncü")

    messages = list_recent_messages(session_id)
    assert [m["text"] for m in messages] == ["birinci", "ikinci", "üçüncü"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_list_recent_messages_caps_but_table_not_pruned(temp_db):
    session_id = _new_session()
    with get_connection() as conn:
        for i in range(MESSAGE_HISTORY_LIMIT + 10):
            append_chat_message(conn, session_id, "user", f"mesaj {i}")

    recent = list_recent_messages(session_id)
    assert len(recent) == MESSAGE_HISTORY_LIMIT
    assert recent[-1]["text"] == f"mesaj {MESSAGE_HISTORY_LIMIT + 9}"  # en yeni son sırada

    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert total == MESSAGE_HISTORY_LIMIT + 10  # tablo budanmadı


def test_process_message_persists_state_and_messages(temp_db, kwargs):
    session_id = _new_session()
    messages = process_message(session_id, "merhaba nasılsın", **kwargs)

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "merhaba nasılsın"

    # DB'de gerçekten kalıcı mı? (aynı transaction'ın dışından, temp_db aynı dosya)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,)
        ).fetchone()
    assert row[0] == 2


def test_process_message_survives_reload_simulating_restart(temp_db, kwargs):
    """Sunucu yeniden başlasa bile yarım kalan konuşma hayatta kalmalı —
    burada bunu iki BAĞIMSIZ process_message çağrısı arasında state'in
    yalnızca DB'den okunarak doğru şekilde ilerlediğini göstererek simüle
    ediyoruz (gerçek bir process restart'ı değil ama aynı garantiyi test
    ediyor: state hiçbir Python değişkeninde taşınmıyor, yalnızca DB'de)."""
    session_id = _new_session()
    process_message(session_id, "ilk mesaj", **kwargs)

    # "Yeniden başlangıç": state'i sıfırdan, yalnızca session_id ile DB'den yüklüyoruz.
    reloaded_state = load_chat_state(session_id)
    assert reloaded_state == ChatState()  # 'other' niyeti akış bırakmaz, ama satır kalıcı

    messages = process_message(session_id, "ikinci mesaj", **kwargs)
    assert len(messages) >= 2
    all_texts = [m["text"] for m in list_recent_messages(session_id)]
    assert "ilk mesaj" in all_texts
    assert "ikinci mesaj" in all_texts


def test_process_message_uses_single_transaction_for_multi_turn_flow(temp_db):
    """create_event akışı 'ask_title' adımına kadar ilerlerse chat_sessions.step
    doğru kaydedilmeli VE candidate_events'e henüz hiçbir satır yazılmamalı
    (save_candidate yalnızca preview_confirm'e girişte çağrılıyor, bkz. chat_flow.py)."""

    class _CreateEventLLM(LLMProvider):
        def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
            if "niyetini sınıflandır" in system_prompt:
                return json.dumps({"intent": "create_event", "query_range_start": None, "query_range_end": None})
            return json.dumps(
                {
                    "event_type": "appointment", "title": None, "start_datetime": None,
                    "duration_minutes": None, "location": None, "ambiguous_fields": [],
                }
            )

        def is_available(self):
            return True

    session_id = _new_session()
    process_message(
        session_id, "bir randevum var",
        llm=_CreateEventLLM(), embedding_provider=_DummyEmbeddingProvider(), calendar=_NullCalendar(), lang="tr",
    )

    with get_connection() as conn:
        row = conn.execute(
            "SELECT flow, step FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        candidate_count = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]

    assert row["flow"] == "create_event"
    assert row["step"] == "ask_title"
    assert candidate_count == 0
