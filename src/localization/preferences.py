"""`localization_preferences` tablosu erişimi (şemada VAR — schema.sql'in son
tablosu — ama bugüne kadar hiçbir Python kodu tarafından kullanılmıyordu).

FK gerçeği: `account_id TEXT PRIMARY KEY REFERENCES accounts(id)` ve her
bağlantıda `PRAGMA foreign_keys = ON` — kayıtlı olmayan bir account_id için
yazmak `FOREIGN KEY constraint failed` ile patlar. Hiç hesap yokken de dil
tercihinin çalışması gerektiğinden (bkz. src/ui/session.py'nin cookie +
user_preferences geri düşüş katmanları), buradaki yazma fonksiyonları
kayıtlı olmayan bir hesap için SESSİZCE no-op olur — bayat bir cookie'nin
işaret ettiği silinmiş bir hesap 500'e dönüşmesin."""

from __future__ import annotations

from src.services.timeutil import DEFAULT_TIMEZONE
from src.storage.db import get_connection


def get_localization_preference(account_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT account_id, ui_language, date_format_pref, timezone "
            "FROM localization_preferences WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    return dict(row) if row else None


def _account_exists(conn, account_id: str) -> bool:
    return conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is not None


def set_ui_language(account_id: str, ui_language: str) -> None:
    with get_connection() as conn:
        if not _account_exists(conn, account_id):
            return
        conn.execute(
            """
            INSERT INTO localization_preferences (account_id, ui_language)
            VALUES (?, ?)
            ON CONFLICT(account_id) DO UPDATE SET ui_language = excluded.ui_language
            """,
            (account_id, ui_language),
        )


def set_timezone(account_id: str, tz_name: str) -> None:
    with get_connection() as conn:
        if not _account_exists(conn, account_id):
            return
        conn.execute(
            """
            INSERT INTO localization_preferences (account_id, timezone)
            VALUES (?, ?)
            ON CONFLICT(account_id) DO UPDATE SET timezone = excluded.timezone
            """,
            (account_id, tz_name),
        )


def get_effective_timezone(account_id: str | None) -> str:
    if account_id:
        pref = get_localization_preference(account_id)
        if pref and pref.get("timezone"):
            return pref["timezone"]
    return DEFAULT_TIMEZONE
