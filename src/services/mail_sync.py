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

from src.connectors.account_registry import get_account
from src.connectors.gmail import GmailConnector
from src.connectors.outlook import OutlookConnector
from src.core.models import UnifiedEmail
from src.storage.db import get_connection

BODY_EXCERPT_MAX_CHARS = 2000


def _get_sync_cursor(account_id: str, sync_provider: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider_cursor_or_history_id FROM sync_states WHERE provider = ? AND account_id = ?",
            (sync_provider, account_id),
        ).fetchone()
    return row["provider_cursor_or_history_id"] if row else None


def get_sync_state(account_id: str, provider: str = "gmail") -> dict | None:
    """Ana Sayfa'nın "son tarama" göstergesi için — `_get_sync_cursor`'dan
    farklı olarak `last_sync_at`'i de döner (o yalnızca cursor'ı okuyordu,
    tarama mantığının iç kullanımı için yeterliydi)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_sync_at, provider_cursor_or_history_id FROM sync_states "
            "WHERE provider = ? AND account_id = ?",
            (provider, account_id),
        ).fetchone()
    return dict(row) if row else None


def _save_sync_cursor(account_id: str, sync_provider: str, cursor: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sync_states (provider, account_id, last_sync_at, provider_cursor_or_history_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider, account_id) DO UPDATE SET
                last_sync_at = excluded.last_sync_at,
                provider_cursor_or_history_id = excluded.provider_cursor_or_history_id
            """,
            (sync_provider, account_id, now, cursor),
        )


def _upsert_email_thread(account_id: str, email: UnifiedEmail) -> None:
    """email_messages.thread_id, email_threads(thread_id)'e foreign key ile
    bağlı — mesajı kaydetmeden önce thread kaydının var olması gerekir
    (canlı testte FOREIGN KEY constraint failed hatasıyla ortaya çıktı)."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT languages_seen FROM email_threads WHERE thread_id = ?",
            (email.thread_id,),
        ).fetchone()
        if existing:
            languages = set(json.loads(existing["languages_seen"] or "[]"))
            if email.detected_language:
                languages.add(email.detected_language)
            conn.execute(
                "UPDATE email_threads SET languages_seen = ?, last_message_at = ? WHERE thread_id = ?",
                (json.dumps(sorted(languages)), email.received_at.isoformat(), email.thread_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO email_threads (thread_id, account_id, participants, languages_seen, last_message_at)
                VALUES (?,?,?,?,?)
                """,
                (
                    email.thread_id,
                    account_id,
                    json.dumps([email.sender, *email.recipients]),
                    json.dumps([email.detected_language] if email.detected_language else []),
                    email.received_at.isoformat(),
                ),
            )


def _upsert_email_message(account_id: str, email: UnifiedEmail) -> tuple[str, bool]:
    """Döner: (email_messages.id, already_processed). Aynı (account_id,
    message_id) daha önce kaydedilmişse INSERT sessizce yok sayılır, mevcut
    satırın id'si ve processed durumu döner."""
    _upsert_email_thread(account_id, email)
    excerpt = (email.body_text or "")[:BODY_EXCERPT_MAX_CHARS]
    candidate_row_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO email_messages (
                id, account_id, provider, message_id, thread_id, subject, sender,
                recipients, received_at, detected_language, body_excerpt, labels, processed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
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
                json.dumps(email.labels),
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


def _row_to_unified_email(row) -> UnifiedEmail:
    """Daha önce senkronize edilmiş bir email_messages satırından UnifiedEmail
    yeniden kurar. body_text burada body_excerpt'ten gelir — zaten LLM
    çağrılarına gönderilirken de kısaltılıyor, bu yüzden yeterli."""
    return UnifiedEmail(
        provider=row["provider"],
        account_id=row["account_id"],
        message_id=row["message_id"],
        thread_id=row["thread_id"] or "",
        subject=row["subject"] or "",
        sender=row["sender"] or "",
        recipients=json.loads(row["recipients"] or "[]"),
        received_at=row["received_at"],
        detected_language=row["detected_language"],
        body_text=row["body_excerpt"],
        labels=json.loads(row["labels"] or "[]") if "labels" in row.keys() else [],
    )


def get_unprocessed_emails(account_id: str) -> list[tuple[str, UnifiedEmail]]:
    """Daha önce senkronize edilip henüz analiz edilmemiş (processed=0)
    mailleri döner. Bu, önceki bir çalıştırma tarama bitmeden kesilirse
    (örn. kullanıcı programı kapatırsa) o maillerin bir daha asla
    görünmemesini önler — Gmail'in incremental sync'i (historyId cursor)
    onları ikinci kez "yeni" olarak döndürmez, bu yüzden yalnızca API'den
    gelen mesajlara güvenmek yetmiyordu (canlı testte fark edildi)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM email_messages WHERE account_id = ? AND processed = 0",
            (account_id,),
        ).fetchall()
    return [(row["id"], _row_to_unified_email(row)) for row in rows]


def sync_new_emails(account_id: str) -> list[tuple[str, UnifiedEmail]]:
    """Yeni mailleri çeker, email_messages'a kaydeder, ve hâlâ analiz
    edilmemiş TÜM mailleri döner (bu çalıştırmada yeni gelenler + önceki bir
    çalıştırmadan kalan işlenmemişler — bkz. get_unprocessed_emails).

    Hesabın sağlayıcısına göre GmailConnector/OutlookConnector arasında
    dallanır — canlı testte bulunan bir hatanın düzeltmesi: önceden burası
    KOŞULSUZ GmailConnector kuruyordu, bir Outlook
    hesabıyla çağrılınca o hesap için hiç Google token'ı olmadığından
    interaktif Google OAuth akışına düşüp yanlış sağlayıcı için bir tarayıcı
    penceresi açıyordu (bkz. CLAUDE.md Outlook bölümü).

    İlk senkronizasyonda sağlayıcıdan son ~25 mesaj çekilir, sonrakilerde
    yalnızca cursor'dan (Gmail: historyId, Outlook: delta link) beri
    gelenler (bkz. ilgili connector'ın list_new_messages'ı)."""
    account = get_account(account_id)
    if account is not None and account["provider"] == "outlook":
        sync_provider = "outlook"
        connector = OutlookConnector(account_id=account_id)
    else:
        sync_provider = "gmail"
        connector = GmailConnector(account_id=account_id)

    cursor = _get_sync_cursor(account_id, sync_provider)
    messages, new_cursor = connector.list_new_messages(cursor)
    _save_sync_cursor(account_id, sync_provider, new_cursor)

    for email in messages:
        _upsert_email_message(account_id, email)

    return get_unprocessed_emails(account_id)
