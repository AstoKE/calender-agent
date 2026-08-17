"""Account Registry (bkz. docs/architecture-plan.md §6/§12).

`accounts` tablosu, diğer tüm tabloların (email_messages, sync_states,
calendar_events_cache, ...) account_id foreign key'inin bağlandığı kayıt —
bu kayıt yoksa o tablolara hiçbir şey yazılamaz (canlı testte
`FOREIGN KEY constraint failed` hatasıyla ortaya çıktı).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.storage.db import get_connection


def _derive_account_id(email: str) -> str:
    """E-postanın @ öncesi kısmından dosya adı/SQL için güvenli bir account_id türetir."""
    local_part = email.split("@")[0].lower()
    return re.sub(r"[^a-z0-9_-]", "_", local_part)


def list_accounts() -> list[dict]:
    """Kayıtlı tüm hesapları (id, provider, email, status) bağlanma sırasına göre döner."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, provider, email, status FROM accounts ORDER BY connected_at"
        ).fetchall()
    return [dict(row) for row in rows]


def select_account() -> tuple[str, str]:
    """CLI'da kayıtlı hesaplardan birini seçtirir ya da yeni bir hesap ekletir.

    Her connector (GmailConnector/GoogleCalendarConnector) account_id'ye özel
    ayrı bir OAuth token dosyası kullandığından (bkz. google_auth.py), birden
    fazla hesap DB'de yan yana kayıtlı kalabilir; kullanıcı her çalıştırmada
    hangisiyle devam edeceğini seçer. Döner: (account_id, email).
    """
    accounts = list_accounts()
    print("\nKayıtlı hesaplar:")
    for i, acc in enumerate(accounts, start=1):
        print(f"  {i}. {acc['email']}")
    add_new_index = len(accounts) + 1
    print(f"  {add_new_index}. Yeni hesap ekle")

    choice = input("Hangi hesabı kullanmak istiyorsunuz? ").strip()
    try:
        index = int(choice)
    except ValueError:
        index = -1

    if 1 <= index <= len(accounts):
        acc = accounts[index - 1]
        return acc["id"], acc["email"]

    email = input("Eklenecek Gmail adresi: ").strip()
    account_id = _derive_account_id(email)
    ensure_account_registered(account_id, provider="google", email=email)
    return account_id, email


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
