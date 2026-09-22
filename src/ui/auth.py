"""Uygulama girişi (bkz. plan "Real login (Gmail/Outlook) + per-user account
ownership") — `src/ui/session.py`nin ("aktif hesap", cookie-tabanlı, auth
YOK) yanına eklenen gerçek bir kimlik/oturum katmanı.

`sessions` tablosu opak bir rastgele token tutar (imzalı bir JWT DEĞİL —
chat_sessions'la AYNI "DB'de tut, imzalama anahtarı yönetme" deseni,
bkz. schema.sql). Bu modüldeki fonksiyonlar `session.py`'nin fonksiyonları
gibi ASLA istisna fırlatmaz — `get_current_user` bozuk/süresi dolmuş/
eksik bir cookie'de sessizce None döner, çağıran (auth_guard_middleware)
bunu "giriş yapılmamış" olarak yorumlayıp /giris'e yönlendirir."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request

from src.storage.db import get_connection

SESSION_COOKIE = "session_token"
# AUTH-03 (bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md): önceden 400 gün
# sabitti ve HİÇ yenilenmiyordu (bir kez oluşturulup asla dokunulmuyordu) —
# "daha kısa ve yenilenebilir oturum politikası" hedefine göre şimdi hem
# daha kısa (30 gün) hem de KAYAN bir pencere: her başarılı doğrulamada
# `touch_session` süreyi yeniden 30 güne uzatıyor (bkz. get_current_user),
# yani aktif bir kullanıcı hiç çıkış yapmaz, yalnızca 30 gün boyunca HİÇ
# istek atmayan bir oturum düşer.
SESSION_MAX_AGE_DAYS = 30


def create_user(email: str) -> dict:
    """Bu email için bir `users` satırı yoksa oluşturur, varsa olduğu gibi
    döner (idempotent — `ensure_account_registered`'daki AYNI desen)."""
    with get_connection() as conn:
        existing = conn.execute("SELECT id, email, created_at FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return dict(existing)
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)",
            (user_id, email, now),
        )
        return {"id": user_id, "email": email, "created_at": now}


def get_user_by_email(email: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT id, email, created_at FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_MAX_AGE_DAYS)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat()),
        )
    return token


def touch_session(token: str) -> None:
    """Bir oturumun `expires_at`'ini şu andan itibaren yeniden `SESSION_MAX_AGE_DAYS`
    güne uzatır — KAYAN pencere (bkz. SESSION_MAX_AGE_DAYS notu). `get_current_user`
    her başarılı doğrulamada bunu çağırır; aktif kullanıcı hiç dışarı atılmaz."""
    new_expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_MAX_AGE_DAYS)).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (new_expires_at, token))


def get_current_user(request: Request) -> dict | None:
    """Cookie'deki token'ı `sessions`'a, oradan `users`'a çözer. Token yok/
    bilinmiyor/süresi dolmuşsa (ya da herhangi bir ayrıştırma hatası)
    SESSİZCE None — asla istisna fırlatmaz (bkz. modül docstring'i)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT s.expires_at, u.id, u.email, u.created_at
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token = ?
                """,
                (token,),
            ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            return None
        touch_session(token)
        return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}
    except Exception:
        return None


def destroy_session(token: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def adopt_orphaned_data(user_id: str) -> None:
    """Yalnızca açıkça istenen yerel veri taşıması için toplu sahiplik ataması.

    Web giriş/hesap bağlama akışları bu fonksiyonu ÇAĞIRMAMALI. Sahipsiz
    verinin kime ait olduğu login ile kanıtlanamaz. Yerel operatör tüm eski
    verinin hedef kullanıcıya ait olduğunu doğruladığında kullanılabilir.
    Mevcut sahipli kayıtlar değiştirilmez.
    """
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET user_id = ? WHERE user_id IS NULL", (user_id,))
        conn.execute("UPDATE user_preferences SET user_id = ? WHERE user_id IS NULL", (user_id,))
        conn.execute("UPDATE personal_policies SET user_id = ? WHERE user_id IS NULL", (user_id,))
        conn.execute("UPDATE user_corrections SET user_id = ? WHERE user_id IS NULL", (user_id,))
