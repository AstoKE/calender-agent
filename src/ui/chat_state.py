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

from fastapi import Request

from src.providers.base import FileInputCapable
from src.services.chat_flow import ChatState, advance
from src.storage.db import get_connection
from src.ui.chat_session import get_current_chat_session_id

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
    session_id: str,
    user_text: str,
    *,
    llm,
    embedding_provider,
    calendar,
    lang: str,
    display_text: str | None = None,
    file_bytes: bytes | None = None,
    file_mime_type: str | None = None,
) -> list[dict]:
    """Bir sohbet turunu uçtan uca işler: state'i yükler, kullanıcı mesajını
    yazar, `advance()`'i HİÇBİR bağlantı açık değilken çalıştırır (bkz. modül
    docstring'i — LLM/takvim çağrıları saniyeler sürebilir), sonra yeni
    state'i + asistan mesajlarını ayrı bir kısa bağlantıda yazar. Son N
    mesajı (bu tur dahil) kronolojik sırada döner.

    `display_text` (verilirse) mesaj balonunda GÖSTERİLEN metin, `user_text`
    ise `advance()`'e giden ve akışı ilerleten HAM değer — chat_routes.py bir
    hızlı-yanıt butonuna tıklanınca (örn. action="approve") bunları BİLİNÇLİ
    olarak ayırıyor: "approve" gibi ham İngilizce token'ın kendisi işleme
    doğru şekilde gitmeli, ama kullanıcıya "approve" yazan bir balon
    göstermek yerine düğmenin kendi çevrilmiş etiketi ("Onayla") gösterilir
    (bkz. canlı testte bulunan UI kusuru). Bir dosya eklendiğinde de aynı
    ayrım işe yarıyor: chat_routes.py balonda "Dosya gönderildi" gösterir,
    `advance()`'e giden `file_bytes`/`file_mime_type` ise gerçek veri."""
    state = load_chat_state(session_id)

    with get_connection() as conn:
        append_chat_message(conn, session_id, "user", display_text if display_text is not None else user_text)

    new_state, replies = advance(
        state, user_text,
        llm=llm, embedding_provider=embedding_provider, calendar=calendar, lang=lang,
        file_bytes=file_bytes, file_mime_type=file_mime_type,
    )

    with get_connection() as conn:
        for reply in replies:
            append_chat_message(conn, session_id, "assistant", reply)
        save_chat_state(conn, session_id, new_state)

    return list_recent_messages(session_id)


def widget_context_for_session(session_id: str | None, *, vision_enabled: bool = False) -> dict:
    """`_asistan_chat.html`'in ihtiyaç duyduğu `chat_messages`/`chat_step`'i
    BİLİNEN bir `session_id`'den üretir. `chat_routes.py::send_chat_message`
    yeni bir oturum açtığında (bkz. get_or_create_chat_session) bunu doğrudan
    kullanır — `build_chat_widget_context`'in cookie'den okuma yoluna
    GÜVENEMEZ, çünkü o cookie'yi taşıyan Set-Cookie başlığı henüz TARAYICIYA
    gitmemiştir; aynı istek nesnesinin `request.cookies`'i hâlâ eski (cookie'siz)
    hâlini gösterir — canlı testte tam olarak bu yüzden yeni açılan bir
    oturumun ilk mesajı fragment'ta hiç görünmüyordu, bulunup düzeltildi.

    `vision_enabled` (bkz. FileInputCapable): şablonun "dosya ekle" butonunu
    gösterip göstermeyeceği — varsayılan (Foundry Local) provider dosya
    girişini desteklemiyor, çağıran bunu `request.app.state.llm`'den
    hesaplayıp geçirmeli (bkz. build_chat_widget_context)."""
    if not session_id:
        return {"chat_messages": [], "chat_step": None, "vision_enabled": vision_enabled}
    return {
        "chat_messages": list_recent_messages(session_id),
        "chat_step": load_chat_state(session_id).step,
        "vision_enabled": vision_enabled,
    }


def build_chat_widget_context(request: Request, account_id: str | None) -> dict:
    """`widget_context_for_session`'ın cookie'den session_id OKUYAN hâli —
    tam sayfa `GET /anasayfa` (routes.py::home_page) ve zaten var olan bir
    oturuma karşı çalışan AJAX yollarında (bkz. chat_routes.py) kullanılır.
    Salt okunur: hesap yoksa ya da hiç sohbet edilmemişse boş boş bir
    chat_sessions satırı oluşturmaz."""
    vision_enabled = isinstance(getattr(request.app.state, "llm", None), FileInputCapable)
    if not account_id:
        return widget_context_for_session(None, vision_enabled=vision_enabled)
    return widget_context_for_session(
        get_current_chat_session_id(request, account_id), vision_enabled=vision_enabled
    )
