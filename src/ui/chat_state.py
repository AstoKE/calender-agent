"""`ChatState` kalıcılığı: `chat_sessions.state_json` ↔ `ChatState`,
`chat_messages` yazma/okuma (bkz. plan "Web Chatbox" Faz 4).

`src/services/chat_flow.py`'yi DB'den ayrı tutmak için ince katman — bu
modül `state_json`'ı okuyup/yazan ve mesaj geçmişini saklayan kısmı üstleniyor.
`process_message` tek bir sohbet turunu uçtan uca yürütür — `src/ui/chat_routes.py`
bunu çağıran ince bir HTTP katmanı.

ÖNEMLİ: `advance()` çağrısı (LLM/takvim API'si dahil, CPU'da 10-30sn sürebilen
bir işlem) ASLA açık bir SQLite bağlantısı/transaction'ı İÇİNDE yapılmaz —
canlı testte tam olarak bunun tersi (append-user-message + advance() +
append-assistant-messages + save-state'in TEK bir `get_connection()` transaction'ı
içine alınması) `sqlite3.OperationalError: database is locked` hatasına yol
açtı (advance() süresince açık kalan yazma kilidi, aynı anda gelen başka bir
isteği — örn. `/asistan/sifirla` — 5sn'lik varsayılan busy-timeout'u aşınca
500'e düşürdü). Bu yüzden `process_message` üç AYRI kısa bağlantı kullanır:
kullanıcı mesajını yaz (anlık) -> `advance()`'i HİÇ bağlantı açık değilken
çalıştır (kendi yazma noktaları kendi kısa bağlantılarını açar, bkz.
chat_flow.py) -> asistan mesajlarını + son state'i yaz (anlık)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.services.chat_flow import ChatState, advance
from src.storage.db import get_connection

MESSAGE_HISTORY_LIMIT = 30


def load_chat_state(session_id: str) -> ChatState:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT state_json FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return ChatState()
    return ChatState.model_validate_json(row["state_json"])


def save_chat_state(conn, session_id: str, state: ChatState) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE chat_sessions SET flow = ?, step = ?, state_json = ?, updated_at = ? WHERE session_id = ?",
        (state.flow, state.step, state.model_dump_json(), now, session_id),
    )


def append_chat_message(conn, session_id: str, role: str, text: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, text, created_at) VALUES (?,?,?,?)",
        (session_id, role, text, now),
    )


def list_recent_messages(session_id: str, limit: int = MESSAGE_HISTORY_LIMIT) -> list[dict]:
    """Son `limit` mesajı KRONOLOJİK sırada döner (DESC sorgulanıp Python'da
    ters çevrilir — bkz. plan: yalnızca render sorgusu sınırlı, `chat_messages`
    tablosunun kendisi hiç budanmıyor, `audit_logs` gibi tam kalıyor)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, text, created_at FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def process_message(
    session_id: str, user_text: str, *, llm, embedding_provider, calendar, lang: str
) -> list[dict]:
    """Bir sohbet turunu uçtan uca işler: state'i yükler, kullanıcı mesajını
    yazar, `advance()`'i HİÇBİR bağlantı açık değilken çalıştırır (bkz. modül
    docstring'i — LLM/takvim çağrıları saniyeler sürebilir), sonra yeni
    state'i + asistan mesajlarını ayrı bir kısa bağlantıda yazar. Son N
    mesajı (bu tur dahil) kronolojik sırada döner."""
    state = load_chat_state(session_id)

    with get_connection() as conn:
        append_chat_message(conn, session_id, "user", user_text)

    new_state, replies = advance(
        state, user_text,
        llm=llm, embedding_provider=embedding_provider, calendar=calendar, lang=lang,
    )

    with get_connection() as conn:
        for reply in replies:
            append_chat_message(conn, session_id, "assistant", reply)
        save_chat_state(conn, session_id, new_state)

    return list_recent_messages(session_id)
