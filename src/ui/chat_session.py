"""Sohbet oturumu: cookie + `chat_sessions` satırı yönetimi (bkz. plan "Web
Chatbox"). `src/ui/session.py`'nin aktif hesap/dil cookie desenini izler —
ama o yalnızca cookie okuyup DB'ye hiç dokunmuyordu; bu modül `chat_sessions`
tablosunu da yönetiyor, çünkü konuşma durumu (hangi adımda olduğumuz,
kısmen dolu candidate, vb. — bkz. `src/services/chat_flow.py`) SQLite'ta
kalıcı olmalı, sunucu yeniden başlasa bile hayatta kalmalı."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Request, Response

from src.storage.db import get_connection

CHAT_SESSION_COOKIE = "chat_session"
CHAT_COOKIE_MAX_AGE = 400 * 24 * 3600  # src/ui/session.py::COOKIE_MAX_AGE ile aynı üst sınır


def _insert_new_session(account_id: str) -> str:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, account_id, flow, step, state_json, created_at, updated_at) "
            "VALUES (?,?,NULL,NULL,'{}',?,?)",
            (session_id, account_id, now, now),
        )
    return session_id


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        CHAT_SESSION_COOKIE, session_id, max_age=CHAT_COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
    )


def get_or_create_chat_session(request: Request, response: Response, account_id: str) -> str:
    """Cookie'deki `session_id` `chat_sessions`'ta hâlâ var VE aynı
    `account_id`'ye aitse onu döner. Aksi halde (cookie yok, DB'de yok, ya
    da BAŞKA bir hesaba ait — kullanıcı `/hesap-sec` ile hesap değiştirmiş)
    sessizce YENİ bir oturum açar — `resolve_active_account`'ın "cookie
    geçersizse sessizce ilk hesaba düş" felsefesiyle aynı, ASLA istisna
    fırlatmaz. Bir konuşmanın ortasında hesap değiştirmek CLI'da zaten
    mümkün değildi (`select_account()` bir kere, en başta çağrılıyor) —
    burada da "yeni oturum" en tutarlı geri düşüş: başka bir hesabın
    candidate/etkinlik state'i asla görünmez biçimde karışmaz."""
    cookie_id = request.cookies.get(CHAT_SESSION_COOKIE)
    if cookie_id:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT session_id FROM chat_sessions WHERE session_id = ? AND account_id = ?",
                (cookie_id, account_id),
            ).fetchone()
        if row is not None:
            return cookie_id

    session_id = _insert_new_session(account_id)
    _set_session_cookie(response, session_id)
    return session_id


def create_new_chat_session(response: Response, account_id: str) -> str:
    """"Yeni sohbet" butonu bunu çağırır — `get_or_create_chat_session`'ın
    aksine mevcut cookie'yi HİÇ dikkate almaz, her zaman yeni bir oturum
    açar. ESKİ oturum (session_id'si ve tüm chat_messages'ı) hiç dokunulmadan
    kalır — yalnızca aktif cookie yeni/boş oturuma çevrilir. Eski sohbet
    `list_chat_sessions`/`switch_chat_session` ile ("Geçmiş Sohbetler")
    hâlâ erişilebilir, hiçbir zaman silinmez."""
    session_id = _insert_new_session(account_id)
    _set_session_cookie(response, session_id)
    return session_id


def switch_chat_session(response: Response, account_id: str, session_id: str) -> bool:
    """Kullanıcı "Geçmiş Sohbetler"den bir oturuma dönmek istediğinde
    çağrılır. `session_id`'nin GERÇEKTEN bu hesaba ait olduğunu doğrular
    (başka bir hesabın/URL'den elle denenen rastgele bir session_id'nin
    cookie'ye yazılmasını engellemek için) — bulunamazsa False döner,
    cookie hiç değiştirilmez."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = ? AND account_id = ?",
            (session_id, account_id),
        ).fetchone()
    if row is None:
        return False
    _set_session_cookie(response, session_id)
    return True


def list_chat_sessions(account_id: str, limit: int = 30) -> list[dict]:
    """Hesabın geçmiş sohbetlerini son etkinliğe göre (en yeni önce) döner —
    her satırda liste ekranı için ilk kullanıcı mesajı (önizleme) + mesaj
    sayısı + tarihler. Hiç mesajı olmayan oturumlar (örn. "Yeni sohbet"e
    basılıp hiç yazılmamış) listeden ÇIKARILIR — boş girdilerle geçmişi
    kirletmesin diye."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                cs.session_id, cs.created_at, cs.updated_at,
                (SELECT text FROM chat_messages WHERE session_id = cs.session_id AND role = 'user'
                 ORDER BY id ASC LIMIT 1) AS preview,
                (SELECT COUNT(*) FROM chat_messages WHERE session_id = cs.session_id) AS message_count
            FROM chat_sessions cs
            WHERE cs.account_id = ?
              AND EXISTS (SELECT 1 FROM chat_messages WHERE session_id = cs.session_id)
            ORDER BY cs.updated_at DESC
            LIMIT ?
            """,
            (account_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_current_chat_session_id(request: Request, account_id: str) -> str | None:
    """`get_or_create_chat_session`'ın SALT OKUNUR karşılığı — Ana Sayfa'nın
    `GET` yüklemesinde çağrılır: kullanıcı hiç sohbet etmemişse boş boş
    `chat_sessions` satırı oluşturmaz (yalnızca `POST /asistan/mesaj` ilk
    mesajda oturum açar). Cookie yok/DB'de yok/başka hesaba aitse `None`
    döner — çağıran bunu "henüz sohbet geçmişi yok" olarak ele alır."""
    cookie_id = request.cookies.get(CHAT_SESSION_COOKIE)
    if not cookie_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT session_id FROM chat_sessions WHERE session_id = ? AND account_id = ?",
            (cookie_id, account_id),
        ).fetchone()
    return cookie_id if row is not None else None


def get_chat_session(session_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT session_id, account_id, flow, step, state_json FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None
