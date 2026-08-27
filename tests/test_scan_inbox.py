"""src/services/scan_inbox.py::scan_account_inbox için testler — özellikle
metin bazında takvimlik olmayan ama eki (foto/PDF) olan mailler için yeni
fallback dalı. sync_new_emails/GmailConnector monkeypatch'lenir, gerçek
Gmail API'ye hiç dokunulmaz."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.core.models import Attachment, UnifiedEmail
from src.providers.base import EmbeddingProvider, FileInputCapable, LLMProvider
from src.services import scan_inbox
from src.storage.db import get_connection

_EVENT_JSON = json.dumps({
    "event_type": "meeting", "title": "Proje Toplantısı", "start_datetime": "2026-09-01T14:00:00",
    "duration_minutes": 60, "location": None, "ambiguous_fields": [],
})


class _DummyEmbeddingProvider(EmbeddingProvider):
    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    @property
    def model_name(self):
        return "dummy-embedding"

    @property
    def dimension(self):
        return 8


class _NotWorthyTextButVisionCapableLLM(LLMProvider, FileInputCapable):
    """Metin sınıflandırması hep False döner (`is_calendar_worthy`'nin
    beklediği JSON şekli) — asıl bilgi ekte."""

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return json.dumps({"is_calendar_worthy": False, "reason": "metin belirsiz"})

    def generate_from_file(self, system_prompt, user_prompt, file_bytes, mime_type, json_output=False):
        return _EVENT_JSON

    def is_available(self):
        return True


class _NotWorthyTextOnlyLLM(LLMProvider):
    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return json.dumps({"is_calendar_worthy": False, "reason": "alakasız"})

    def is_available(self):
        return True


class _FakeGmailConnector:
    def __init__(self, account_id):
        self.account_id = account_id

    def download_attachment(self, message_id, attachment_id):
        return b"FAKE_IMAGE_BYTES"


def _insert_account_and_email(account_id="acc1", email_id=None):
    email_id = email_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (id, provider, account_type, email, connected_at, status) "
            "VALUES (?, 'google', 'personal', ?, ?, 'active')",
            (account_id, f"{account_id}@example.com", now),
        )
        conn.execute(
            "INSERT INTO email_threads (thread_id, account_id, participants, languages_seen, last_message_at) "
            "VALUES (?,?,?,?,?)",
            (f"thread-{email_id}", account_id, "[]", "[]", now),
        )
        conn.execute(
            """
            INSERT INTO email_messages (
                id, account_id, provider, message_id, thread_id, subject, sender,
                recipients, received_at, detected_language, body_excerpt, labels, processed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (email_id, account_id, "gmail", f"msg-{email_id}", f"thread-{email_id}", "Davetiye",
             "alerts@example.com", "[]", now, "tr", "", "[]"),
        )
    return email_id


def test_not_worthy_with_no_attachments_is_just_skipped(temp_db, monkeypatch):
    email_id = _insert_account_and_email()
    email = UnifiedEmail(
        provider="gmail", account_id="acc1", message_id=f"msg-{email_id}", thread_id=f"thread-{email_id}",
        subject="Davetiye", sender="alerts@example.com", recipients=[],
        received_at=datetime.now(timezone.utc), body_text="", attachments=[],
    )
    monkeypatch.setattr(scan_inbox, "sync_new_emails", lambda account_id: [(email_id, email)])

    result = scan_inbox.scan_account_inbox("acc1", _NotWorthyTextOnlyLLM(), _DummyEmbeddingProvider())

    assert result == {"total": 1, "candidates_found": 0, "skipped_errors": 0}
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0] == 0
        assert conn.execute("SELECT processed FROM email_messages WHERE id = ?", (email_id,)).fetchone()[0] == 1


def test_not_worthy_but_has_attachment_and_vision_llm_saves_candidate(temp_db, monkeypatch):
    email_id = _insert_account_and_email()
    email = UnifiedEmail(
        provider="gmail", account_id="acc1", message_id=f"msg-{email_id}", thread_id=f"thread-{email_id}",
        subject="Davetiye", sender="alerts@example.com", recipients=[],
        received_at=datetime.now(timezone.utc), body_text="ekteki davetiyeye bakınız",
        attachments=[Attachment(filename="davetiye.jpg", content_type="image/jpeg", attachment_id="att1")],
    )
    monkeypatch.setattr(scan_inbox, "sync_new_emails", lambda account_id: [(email_id, email)])
    monkeypatch.setattr(scan_inbox, "GmailConnector", _FakeGmailConnector)

    result = scan_inbox.scan_account_inbox(
        "acc1", _NotWorthyTextButVisionCapableLLM(), _DummyEmbeddingProvider()
    )

    assert result == {"total": 1, "candidates_found": 1, "skipped_errors": 0}
    with get_connection() as conn:
        row = conn.execute("SELECT title, event_type FROM candidate_events").fetchone()
    assert row["title"] == "Proje Toplantısı"
    assert row["event_type"] == "meeting"


def test_not_worthy_with_attachment_but_text_only_llm_is_skipped(temp_db, monkeypatch):
    # Varsayılan (Foundry Local) LLM FileInputCapable DEĞİL — ek varsa bile
    # görsel yolu hiç denenmemeli, sessizce atlanmalı.
    email_id = _insert_account_and_email()
    email = UnifiedEmail(
        provider="gmail", account_id="acc1", message_id=f"msg-{email_id}", thread_id=f"thread-{email_id}",
        subject="Davetiye", sender="alerts@example.com", recipients=[],
        received_at=datetime.now(timezone.utc), body_text="",
        attachments=[Attachment(filename="davetiye.jpg", content_type="image/jpeg", attachment_id="att1")],
    )
    monkeypatch.setattr(scan_inbox, "sync_new_emails", lambda account_id: [(email_id, email)])

    result = scan_inbox.scan_account_inbox("acc1", _NotWorthyTextOnlyLLM(), _DummyEmbeddingProvider())

    assert result == {"total": 1, "candidates_found": 0, "skipped_errors": 0}
