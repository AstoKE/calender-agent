"""Mail senkronizasyonu (bkz. docs/architecture-plan.md §4/§12).

Sync cursor (Gmail historyId) `sync_states` tablosunda tutulur; her mail
`email_messages`'a yalnızca `body_excerpt` olarak kaydedilir — tam gövde
kalıcı tutulmaz (minimum retention ilkesi, bkz. §13). `processed` bayrağı
aynı mesajın tekrar analiz edilmesini önler (bkz. §11 duplicate önleme).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.connectors.gmail import GmailConnector
from src.core.models import UnifiedEmail
from src.storage.db import get_connection

BODY_EXCERPT_MAX_CHARS = 2000


def _get_sync_cursor(account_id: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider_cursor_or_history_id FROM sync_states WHERE provider = 'gmail' AND account_id = ?",
            (account_id,),
        ).fetchone()
    return row["provider_cursor_or_history_id"] if row else None


def _save_sync_cursor(account_id: str, cursor: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sync_states (provider, account_id, last_sync_at, provider_cursor_or_history_id)
            VALUES ('gmail', ?, ?, ?)
            ON CONFLICT(provider, account_id) DO UPDATE SET
                last_sync_at = excluded.last_sync_at,
                provider_cursor_or_history_id = excluded.provider_cursor_or_history_id
            """,
            (account_id, now, cursor),
        )


def _upsert_email_message(account_id: str, email: UnifiedEmail) -> tuple[str, bool]:
    """Döner: (email_messages.id, already_processed). Aynı (account_id,
    message_id) daha önce kaydedilmişse INSERT sessizce yok sayılır, mevcut
    satırın id'si ve processed durumu döner."""
    excerpt = (email.body_text or "")[:BODY_EXCERPT_MAX_CHARS]
    candidate_row_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO email_messages (
                id, account_id, provider, message_id, thread_id, subject, sender,
                recipients, received_at, detected_language, body_excerpt, processed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                candidate_row_id,
                account_id,
                email.provider,
                email.message_id,
                email.thread_id,
                email.subject,
                email.sender,
                json.dumps(email.recipients),
                email.received_at.isoformat(),
                email.detected_language,
                excerpt,
            ),
        )
        row = conn.execute(
            "SELECT id, processed FROM email_messages WHERE account_id = ? AND message_id = ?",
            (account_id, email.message_id),
        ).fetchone()
    return row["id"], bool(row["processed"])


def mark_email_processed(email_row_id: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE email_messages SET processed = 1 WHERE id = ?", (email_row_id,))


def sync_new_emails(account_id: str) -> list[tuple[str, UnifiedEmail]]:
    """Yeni mailleri çeker ve email_messages'a kaydeder.

    Dönüş: daha önce işlenmemiş [(email_messages.id, UnifiedEmail), ...] —
    ilk senkronizasyonda son ~25 mesaj, sonrakilerde yalnızca historyId
    cursor'ından beri gelenler (bkz. GmailConnector.list_new_messages)."""
    gmail = GmailConnector(account_id=account_id)
    cursor = _get_sync_cursor(account_id)
    messages, new_cursor = gmail.list_new_messages(cursor)
    _save_sync_cursor(account_id, new_cursor)

    unprocessed: list[tuple[str, UnifiedEmail]] = []
    for email in messages:
        row_id, already_processed = _upsert_email_message(account_id, email)
        if not already_processed:
            unprocessed.append((row_id, email))
    return unprocessed
