"""Account Registry (bkz. docs/architecture-plan.md §6/§12).

`accounts` tablosu, diğer tüm tabloların (email_messages, sync_states,
calendar_events_cache, ...) account_id foreign key'inin bağlandığı kayıt —
bu kayıt yoksa o tablolara hiçbir şey yazılamaz (canlı testte
`FOREIGN KEY constraint failed` hatasıyla ortaya çıktı).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.storage.db import get_connection

# MVP: tek, sabit kodlanmış hesap (çoklu hesap yönetimi/"E-posta Hesapları"
# ekranı §16 kapsamında ileride eklenecek). Hem GmailConnector hem
# GoogleCalendarConnector aynı account_id'yi kullanır (bkz. GOOGLE_ACCOUNT_SCOPES).
ACCOUNT_ID = "astokrappersteam"
ACCOUNT_EMAIL = "astokrappersteam@gmail.com"


def ensure_account_registered(
    account_id: str,
    provider: str,
    email: str,
    account_type: str = "personal",
) -> None:
    """`accounts` tablosunda bu account_id için kayıt yoksa oluşturur (idempotent)."""
    with get_connection() as conn:
        existing = conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if existing:
            return
        conn.execute(
            """
            INSERT INTO accounts (id, provider, account_type, email, connected_at, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (account_id, provider, account_type, email, datetime.now(timezone.utc).isoformat()),
        )
