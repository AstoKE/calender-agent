"""src/services/mail_sync.py::sync_new_emails için sağlayıcı dallanması
testleri — canlı testte bulunan bir hatanın (her zaman GmailConnector
kuruluyordu, bir Outlook hesabıyla çağrılınca yanlış sağlayıcı için
interaktif OAuth'a düşüyordu, bkz. CLAUDE.md) regresyon testi.
GmailConnector/OutlookConnector monkeypatch'lenir, gerçek Google/Microsoft
API'sine hiç dokunulmaz."""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.models import EmailProvider, UnifiedEmail
from src.services import mail_sync
from src.storage.db import get_connection


def _insert_account(account_id: str, provider: str, email: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO accounts (id, provider, account_type, email, connected_at, status) "
            "VALUES (?, ?, 'personal', ?, ?, 'active')",
            (account_id, provider, email, now),
        )


class _ExplodingConnector:
    """Yanlış sağlayıcı için hiç kurulmaması gereken bir connector'ı
    temsil eder — kurulursa test kasıtlı olarak patlar."""

    def __init__(self, account_id):
        raise AssertionError(f"Bu connector {account_id!r} için HİÇ kurulmamalıydı")


class _FakeOutlookConnector:
    def __init__(self, account_id):
        self.account_id = account_id

    def list_new_messages(self, since_cursor):
        assert since_cursor is None  # ilk senkron
        email = UnifiedEmail(
            provider=EmailProvider.OUTLOOK, account_id=self.account_id,
            message_id="msg1", thread_id="thread1", subject="Toplantı daveti",
            sender="alerts@example.com", recipients=[], received_at=datetime.now(timezone.utc),
            body_text="", attachments=[],
        )
        return [email], "DELTA_LINK_1"


class _FakeGmailConnector:
    def __init__(self, account_id):
        self.account_id = account_id

    def list_new_messages(self, since_cursor):
        assert since_cursor is None
        email = UnifiedEmail(
            provider=EmailProvider.GMAIL, account_id=self.account_id,
            message_id="msg1", thread_id="thread1", subject="Toplantı daveti",
            sender="alerts@example.com", recipients=[], received_at=datetime.now(timezone.utc),
            body_text="", attachments=[],
        )
        return [email], "HISTORY_ID_1"


def test_outlook_account_uses_outlook_connector_not_gmail(temp_db, monkeypatch):
    _insert_account("outlook_acc", "outlook", "user@hotmail.com")
    monkeypatch.setattr(mail_sync, "OutlookConnector", _FakeOutlookConnector)
    monkeypatch.setattr(mail_sync, "GmailConnector", _ExplodingConnector)

    result = mail_sync.sync_new_emails("outlook_acc")

    assert len(result) == 1
    _, email = result[0]
    assert email.provider == "outlook"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider, provider_cursor_or_history_id FROM sync_states WHERE account_id = ?",
            ("outlook_acc",),
        ).fetchone()
    assert row["provider"] == "outlook"
    assert row["provider_cursor_or_history_id"] == "DELTA_LINK_1"


def test_google_account_still_uses_gmail_connector(temp_db, monkeypatch):
    _insert_account("test_google_acc", "google", "user@gmail.com")
    monkeypatch.setattr(mail_sync, "GmailConnector", _FakeGmailConnector)
    monkeypatch.setattr(mail_sync, "OutlookConnector", _ExplodingConnector)

    result = mail_sync.sync_new_emails("test_google_acc")

    assert len(result) == 1
    _, email = result[0]
    assert email.provider == "gmail"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider, provider_cursor_or_history_id FROM sync_states WHERE account_id = ?",
            ("test_google_acc",),
        ).fetchone()
    assert row["provider"] == "gmail"
    assert row["provider_cursor_or_history_id"] == "HISTORY_ID_1"
