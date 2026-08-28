"""src/memory/correction_memory.py'nin okuma fonksiyonları için testler
(bkz. plan Faz 8) — list_corrections/get_correction/count_corrections/
set_correction_future_use/delete_correction. Kaydetme fonksiyonları
(save_user_correction vb.) zaten canlı testte doğrulanmıştı; burada
Düzeltmelerim ekranının okuma yolu test ediliyor.

`user_corrections.candidate_id` `candidate_events(candidate_id)`'ye FK
verdiğinden (PRAGMA foreign_keys=ON), önce gerçek bir candidate satırı
(hesap+mail+candidate zinciri) kurulması gerekiyor — bkz. test_candidate_store.py'deki
aynı desen."""

import uuid
from datetime import datetime, timezone

from src.candidates.store import save_new_candidate
from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.memory.correction_memory import (
    count_corrections,
    delete_correction,
    get_correction,
    list_corrections,
    mark_correction_approved,
    save_user_correction,
    set_correction_future_use,
)
from src.policies.store import add_policy, get_policy
from src.storage.db import get_connection


def _insert_account_and_email(account_id="acc1", email_id=None, sender="alerts@example.com"):
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
            (email_id, account_id, "gmail", f"msg-{email_id}", f"thread-{email_id}", "Test mail", sender,
             "[]", now, "tr", "", "[]"),
        )
    return email_id


def _candidate(**overrides) -> CandidateEvent:
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
        status=CandidateStatus.READY_FOR_CONFIRMATION,
        extraction_reason="test",
    )
    fields.update(overrides)
    return CandidateEvent(**fields)


def _persisted_candidate(**overrides) -> CandidateEvent:
    email_id = _insert_account_and_email()
    candidate = _candidate(**overrides)
    save_new_candidate(candidate, source_email_row_id=email_id)
    return candidate


def test_list_corrections_empty(temp_db):
    assert list_corrections() == []


def test_save_and_list_correction_round_trips_snapshots(temp_db):
    candidate = _persisted_candidate()
    save_user_correction(candidate, "yanlış süre")

    corrections = list_corrections()
    assert len(corrections) == 1
    c = corrections[0]
    assert c["user_feedback_text"] == "yanlış süre"
    assert c["original_output"] == c["corrected_output"]  # reddetme akışı: candidate düzenlenmemiş
    assert c["correction_type"] is None
    assert c["derived_policy_id"] is None
    assert c["derived_rule_text"] is None


def test_list_corrections_filters_by_type(temp_db):
    c1 = _persisted_candidate()
    save_user_correction(c1, "alan düzeltmesi")
    c2 = _persisted_candidate()
    save_user_correction(c2, "sınıflandırma düzeltmesi", correction_type="classification")

    assert len(list_corrections(correction_type="field")) == 1
    assert len(list_corrections(correction_type="classification")) == 1
    assert len(list_corrections()) == 2


def test_list_corrections_includes_derived_rule_text(temp_db):
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "toplantılar 90 dk olsun")
    policy = add_policy(
        "default_duration_minutes", "toplantılar 90 dk olsun", {"default_duration_minutes": 90}, event_type="meeting"
    )
    mark_correction_approved(correction.correction_id, policy.policy_id)

    row = list_corrections()[0]
    assert row["derived_policy_id"] == policy.policy_id
    assert row["derived_rule_text"] == "toplantılar 90 dk olsun"
    assert row["derived_rule_active"] is True
    assert row["approved_for_future_use"] is True


def test_get_correction_by_id(temp_db):
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "test")
    assert get_correction(correction.correction_id)["correction_id"] == correction.correction_id
    assert get_correction("olmayan-id") is None


def test_count_corrections(temp_db):
    c1 = _persisted_candidate()
    save_user_correction(c1, "a")
    c2 = _persisted_candidate()
    save_user_correction(c2, "b", correction_type="classification")

    assert count_corrections() == 2
    assert count_corrections("field") == 1
    assert count_corrections("classification") == 1


def test_set_correction_future_use_false_deactivates_derived_policy(temp_db):
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "kural")
    policy = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    mark_correction_approved(correction.correction_id, policy.policy_id)

    set_correction_future_use(correction.correction_id, False)

    assert get_correction(correction.correction_id)["approved_for_future_use"] is False
    assert get_policy(policy.policy_id).active is False


def test_set_correction_future_use_true_does_not_touch_policy(temp_db):
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "kural")
    policy = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    mark_correction_approved(correction.correction_id, policy.policy_id)

    set_correction_future_use(correction.correction_id, True)
    assert get_policy(policy.policy_id).active is True


def test_set_correction_future_use_unknown_id_is_noop(temp_db):
    set_correction_future_use("olmayan-id", False)  # çökmemeli


def test_delete_correction_removes_row_and_embeddings(temp_db):
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "test")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO correction_embeddings (correction_id, embedding, model_name, dim, created_at) "
            "VALUES (?,?,?,?,?)",
            (correction.correction_id, b"\x00", "dummy", 1, datetime.now(timezone.utc).isoformat()),
        )

    delete_correction(correction.correction_id)

    assert get_correction(correction.correction_id) is None
    with get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM correction_embeddings WHERE correction_id = ?", (correction.correction_id,)
        ).fetchone()[0]
    assert remaining == 0


def test_delete_correction_does_not_touch_derived_policy(temp_db):
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "kural")
    policy = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    mark_correction_approved(correction.correction_id, policy.policy_id)

    delete_correction(correction.correction_id)

    assert get_policy(policy.policy_id).active is True


# --- Per-user isolation (bkz. plan "Per-user isolation for rules, corrections, and suggestions") ---
# user_id, users(id)'ye FK verdiği için gerçek kullanıcı satırları gerekiyor.


def _two_users():
    from src.ui.auth import create_user

    return create_user("a@example.com"), create_user("b@example.com")


def test_list_corrections_filters_by_user_id(temp_db):
    user_a, user_b = _two_users()
    c1 = _persisted_candidate()
    save_user_correction(c1, "A'nın düzeltmesi", user_id=user_a["id"])
    c2 = _persisted_candidate()
    save_user_correction(c2, "B'nin düzeltmesi", user_id=user_b["id"])

    assert [c["user_feedback_text"] for c in list_corrections(user_id=user_a["id"])] == ["A'nın düzeltmesi"]
    assert [c["user_feedback_text"] for c in list_corrections(user_id=user_b["id"])] == ["B'nin düzeltmesi"]
    assert len(list_corrections()) == 2


def test_count_corrections_filters_by_user_id(temp_db):
    user_a, user_b = _two_users()
    c1 = _persisted_candidate()
    save_user_correction(c1, "A", user_id=user_a["id"])
    c2 = _persisted_candidate()
    save_user_correction(c2, "B1", user_id=user_b["id"])
    c3 = _persisted_candidate()
    save_user_correction(c3, "B2", correction_type="classification", user_id=user_b["id"])

    assert count_corrections(user_id=user_a["id"]) == 1
    assert count_corrections(user_id=user_b["id"]) == 2
    assert count_corrections("classification", user_id=user_b["id"]) == 1
    assert count_corrections() == 3


def test_row_to_correction_dict_includes_user_id(temp_db):
    user_a, _ = _two_users()
    candidate = _persisted_candidate()
    correction = save_user_correction(candidate, "test", user_id=user_a["id"])
    assert get_correction(correction.correction_id)["user_id"] == user_a["id"]
