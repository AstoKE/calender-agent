import uuid
from datetime import datetime, timezone

from src.candidates.store import (
    apply_update_suggestion,
    find_related_candidate_by_thread,
    get_pending_candidate,
    list_pending_candidates,
    revert_update_suggestion,
    save_new_candidate,
    update_candidate_fields,
    update_candidate_status,
)
from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.storage.db import get_connection


def _insert_account_and_email(
    account_id="acc1", email_id=None, sender="alerts@example.com", subject="Test mail", thread_id=None
):
    email_id = email_id or str(uuid.uuid4())
    thread_id = thread_id or f"thread-{email_id}"
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (id, provider, account_type, email, connected_at, status) "
            "VALUES (?, 'google', 'personal', ?, ?, 'active')",
            (account_id, f"{account_id}@example.com", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO email_threads (thread_id, account_id, participants, languages_seen, last_message_at) "
            "VALUES (?,?,?,?,?)",
            (thread_id, account_id, "[]", "[]", now),
        )
        conn.execute(
            """
            INSERT INTO email_messages (
                id, account_id, provider, message_id, thread_id, subject, sender,
                recipients, received_at, detected_language, body_excerpt, labels, processed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (email_id, account_id, "gmail", f"msg-{email_id}", thread_id, subject, sender,
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


def test_find_related_candidate_by_thread_matches_same_thread(temp_db):
    origin_email_id = _insert_account_and_email(thread_id="thread-shared")
    candidate = _candidate(status=CandidateStatus.ADDED_TO_CALENDAR)
    save_new_candidate(candidate, source_email_row_id=origin_email_id)

    reply_email_id = _insert_account_and_email(email_id="reply-1", thread_id="thread-shared")

    related = find_related_candidate_by_thread("thread-shared", exclude_email_row_id=reply_email_id)
    assert related is not None
    assert related["candidate"].candidate_id == candidate.candidate_id


def test_find_related_candidate_by_thread_ignores_other_threads(temp_db):
    origin_email_id = _insert_account_and_email(thread_id="thread-a")
    candidate = _candidate(status=CandidateStatus.ADDED_TO_CALENDAR)
    save_new_candidate(candidate, source_email_row_id=origin_email_id)

    assert find_related_candidate_by_thread("thread-b", exclude_email_row_id="whatever") is None


def test_find_related_candidate_by_thread_excludes_rejected(temp_db):
    origin_email_id = _insert_account_and_email(thread_id="thread-shared")
    candidate = _candidate(status=CandidateStatus.REJECTED)
    save_new_candidate(candidate, source_email_row_id=origin_email_id)

    reply_email_id = _insert_account_and_email(email_id="reply-1", thread_id="thread-shared")

    assert find_related_candidate_by_thread("thread-shared", exclude_email_row_id=reply_email_id) is None


def test_apply_update_suggestion_snapshots_and_applies_changes(temp_db):
    origin_email_id = _insert_account_and_email(thread_id="thread-shared")
    candidate = _candidate(status=CandidateStatus.ADDED_TO_CALENDAR, location="Oda 101")
    save_new_candidate(candidate, source_email_row_id=origin_email_id)
    reply_email_id = _insert_account_and_email(email_id="reply-1", thread_id="thread-shared")

    apply_update_suggestion(candidate.candidate_id, {"location": "Oda 202"}, reply_email_id)

    result = get_pending_candidate(candidate.candidate_id)
    assert result["candidate"].status == CandidateStatus.UPDATE_SUGGESTED
    assert result["candidate"].location == "Oda 202"
    assert result["previous_snapshot"]["location"] == "Oda 101"

    with get_connection() as conn:
        row = conn.execute(
            "SELECT relation_type FROM candidate_sources WHERE candidate_id = ? AND email_message_id = ?",
            (candidate.candidate_id, reply_email_id),
        ).fetchone()
        assert row["relation_type"] == "update"


def test_revert_update_suggestion_restores_previous_fields(temp_db):
    origin_email_id = _insert_account_and_email(thread_id="thread-shared")
    candidate = _candidate(status=CandidateStatus.ADDED_TO_CALENDAR, location="Oda 101")
    save_new_candidate(candidate, source_email_row_id=origin_email_id)
    reply_email_id = _insert_account_and_email(email_id="reply-1", thread_id="thread-shared")
    apply_update_suggestion(candidate.candidate_id, {"location": "Oda 202"}, reply_email_id)

    revert_update_suggestion(candidate.candidate_id)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, location, previous_snapshot FROM candidate_events WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()
    assert row["status"] == "ADDED_TO_CALENDAR"
    assert row["location"] == "Oda 101"
    assert row["previous_snapshot"] is None


def test_update_suggested_candidate_listed_as_pending(temp_db):
    origin_email_id = _insert_account_and_email(thread_id="thread-shared")
    candidate = _candidate(status=CandidateStatus.ADDED_TO_CALENDAR)
    save_new_candidate(candidate, source_email_row_id=origin_email_id)
    reply_email_id = _insert_account_and_email(email_id="reply-1", thread_id="thread-shared")
    apply_update_suggestion(candidate.candidate_id, {"location": "Oda 202"}, reply_email_id)

    pending = list_pending_candidates()
    assert len(pending) == 1
    assert pending[0]["candidate"].candidate_id == candidate.candidate_id
