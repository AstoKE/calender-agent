import uuid
from datetime import datetime, timezone

from src.candidates.store import (
    get_pending_candidate,
    list_pending_candidates,
    save_new_candidate,
    update_candidate_fields,
    update_candidate_status,
)
from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.storage.db import get_connection


def _insert_account_and_email(account_id="acc1", email_id=None, sender="alerts@example.com", subject="Test mail"):
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
            (email_id, account_id, "gmail", f"msg-{email_id}", f"thread-{email_id}", subject, sender,
             "[]", now, "tr", "", "[]"),
        )
    return email_id


def _candidate(status=CandidateStatus.READY_FOR_CONFIRMATION, **overrides):
    fields = dict(
        candidate_id=str(uuid.uuid4()),
        source_type=SourceType.EMAIL,
        source_references=[],
        source_languages=[],
        event_type=EventType.MEETING,
        title="Proje toplantısı",
        start_datetime=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        duration_minutes=60,
        missing_fields=[],
        ambiguous_fields=[],
        status=status,
        extraction_reason="test",
    )
    fields.update(overrides)
    return CandidateEvent(**fields)


def test_save_and_list_pending_candidate(temp_db):
    email_id = _insert_account_and_email()
    candidate = _candidate()
    save_new_candidate(candidate, source_email_row_id=email_id)

    pending = list_pending_candidates()
    assert len(pending) == 1
    assert pending[0]["candidate"].candidate_id == candidate.candidate_id
    assert pending[0]["account_id"] == "acc1"
    assert pending[0]["sender"] == "alerts@example.com"
    assert pending[0]["subject"] == "Test mail"


def test_added_to_calendar_candidate_not_listed_as_pending(temp_db):
    email_id = _insert_account_and_email()
    candidate = _candidate(status=CandidateStatus.ADDED_TO_CALENDAR)
    save_new_candidate(candidate, source_email_row_id=email_id)

    assert list_pending_candidates() == []


def test_get_pending_candidate_single(temp_db):
    email_id = _insert_account_and_email()
    candidate = _candidate()
    save_new_candidate(candidate, source_email_row_id=email_id)

    result = get_pending_candidate(candidate.candidate_id)
    assert result is not None
    assert result["candidate"].title == "Proje toplantısı"

    assert get_pending_candidate("olmayan-id") is None


def test_update_candidate_status(temp_db):
    email_id = _insert_account_and_email()
    candidate = _candidate()
    save_new_candidate(candidate, source_email_row_id=email_id)

    update_candidate_status(candidate.candidate_id, CandidateStatus.REJECTED)

    # Artık pending sorgusunda görünmemeli (status filtrelenmiş).
    assert get_pending_candidate(candidate.candidate_id) is None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM candidate_events WHERE candidate_id = ?", (candidate.candidate_id,)
        ).fetchone()
        assert row["status"] == "REJECTED"


def test_update_candidate_fields_fills_missing_and_flips_status(temp_db):
    email_id = _insert_account_and_email()
    candidate = _candidate(
        status=CandidateStatus.NEEDS_INFORMATION,
        title=None,
        missing_fields=["title"],
    )
    save_new_candidate(candidate, source_email_row_id=email_id)

    update_candidate_fields(candidate.candidate_id, title="Yeni başlık")

    result = get_pending_candidate(candidate.candidate_id)
    assert result["candidate"].title == "Yeni başlık"
    assert result["candidate"].missing_fields == []
    assert result["candidate"].status == CandidateStatus.READY_FOR_CONFIRMATION


def test_update_candidate_fields_ignores_blank_values(temp_db):
    email_id = _insert_account_and_email()
    candidate = _candidate()
    save_new_candidate(candidate, source_email_row_id=email_id)

    update_candidate_fields(candidate.candidate_id, title="", location=None)

    result = get_pending_candidate(candidate.candidate_id)
    assert result["candidate"].title == "Proje toplantısı"
