from src.core.models import PolicyScope, PolicySource
from src.policies.store import (
    add_policy,
    count_active_policies,
    deactivate_policy,
    deactivate_policy_by_id,
    find_active_conflicting_policy,
    get_active_policies,
    get_active_policies_for_sender,
    get_policy,
    list_policies,
    list_policy_versions,
    normalize_sender,
    reactivate_policy,
)
from src.storage.db import get_connection


def test_normalize_sender_extracts_address_from_display_name():
    assert normalize_sender('"LinkedIn İş İlanı Uyarıları" <jobalerts-noreply@linkedin.com>') == (
        "jobalerts-noreply@linkedin.com"
    )


def test_normalize_sender_is_case_insensitive():
    assert normalize_sender("Someone@Example.COM") == "someone@example.com"


def test_normalize_sender_plain_address_unchanged():
    assert normalize_sender("someone@example.com") == "someone@example.com"


def test_add_policy_event_type_scope(temp_db):
    policy = add_policy(
        category="default_duration_minutes",
        natural_language_rule="Toplantılar 45 dakika olsun",
        structured_action={"default_duration_minutes": 45},
        event_type="meeting",
    )
    assert policy.scope == PolicyScope.EVENT_TYPE
    assert policy.structured_conditions == {"event_type": "meeting"}
    assert policy.source == PolicySource.MANUAL


def test_add_policy_sender_scope(temp_db):
    policy = add_policy(
        category="importance",
        natural_language_rule="LinkedIn mailleri düşük önemli",
        structured_action={"importance": "low"},
        sender='"LinkedIn" <jobalerts-noreply@linkedin.com>',
        source=PolicySource.CORRECTION,
    )
    assert policy.scope == PolicyScope.SENDER
    assert policy.structured_conditions == {"sender": "jobalerts-noreply@linkedin.com"}
    assert policy.source == PolicySource.CORRECTION


def test_add_policy_global_scope(temp_db):
    policy = add_policy(
        category="reminder_minutes_before",
        natural_language_rule="Her zaman 15 dakika önce hatırlat",
        structured_action={"reminder_minutes_before": 15},
    )
    assert policy.scope == PolicyScope.GLOBAL
    assert policy.structured_conditions == {}


def test_get_active_policies_returns_only_active(temp_db):
    add_policy("importance", "kural 1", {"importance": "high"}, event_type="meeting")
    assert len(get_active_policies()) == 1


def test_find_active_conflicting_policy_matches_same_category_and_event_type(temp_db):
    p1 = add_policy("default_duration_minutes", "45 dk", {"default_duration_minutes": 45}, event_type="meeting")
    conflict = find_active_conflicting_policy("default_duration_minutes", event_type="meeting")
    assert conflict is not None
    assert conflict.policy_id == p1.policy_id


def test_find_active_conflicting_policy_ignores_different_event_type(temp_db):
    add_policy("default_duration_minutes", "45 dk toplantı", {"default_duration_minutes": 45}, event_type="meeting")
    conflict = find_active_conflicting_policy("default_duration_minutes", event_type="exam")
    assert conflict is None


def test_find_active_conflicting_policy_ignores_different_category(temp_db):
    add_policy("default_duration_minutes", "45 dk", {"default_duration_minutes": 45}, event_type="meeting")
    conflict = find_active_conflicting_policy("importance", event_type="meeting")
    assert conflict is None


def test_find_active_conflicting_policy_sender_scope_independent_of_event_type(temp_db):
    add_policy("importance", "LinkedIn düşük önemli", {"importance": "low"}, sender="alerts@linkedin.com")
    # Aynı category, aynı sender -> çelişir.
    assert find_active_conflicting_policy("importance", sender="alerts@linkedin.com") is not None
    # Aynı category, event_type scope (sender değil) -> çelişmez (farklı kapsam).
    assert find_active_conflicting_policy("importance", event_type="meeting") is None


def test_get_active_policies_for_sender(temp_db):
    add_policy("importance", "LinkedIn düşük önemli", {"importance": "low"}, sender="alerts@linkedin.com")
    add_policy("default_duration_minutes", "toplantı 45 dk", {"default_duration_minutes": 45}, event_type="meeting")

    matches = get_active_policies_for_sender("alerts@linkedin.com")
    assert len(matches) == 1
    assert matches[0].structured_conditions == {"sender": "alerts@linkedin.com"}

    assert get_active_policies_for_sender("someone-else@example.com") == []


def test_deactivate_policy_marks_inactive_and_snapshots_version(temp_db):
    policy = add_policy("default_duration_minutes", "45 dk", {"default_duration_minutes": 45}, event_type="meeting")
    deactivate_policy(policy)

    assert get_active_policies() == []

    with get_connection() as conn:
        row = conn.execute(
            "SELECT active FROM personal_policies WHERE policy_id = ?", (policy.policy_id,)
        ).fetchone()
        assert row["active"] == 0

        versions = conn.execute(
            "SELECT policy_id, version FROM policy_versions WHERE policy_id = ?", (policy.policy_id,)
        ).fetchall()
        assert len(versions) == 1
        assert versions[0]["version"] == 1


def test_conflict_then_deactivate_versioning_flow(temp_db):
    """Aynı category+event_type'ta ikinci bir politika oluşturulunca, çağıran
    (derive_and_save_policy/derivation.py) find_active_conflicting_policy +
    deactivate_policy'yi bu sırayla kullanarak eskiyi versiyonlar — burada o
    akışı doğrudan simüle ediyoruz (bu oturumda scripted olarak canlı test
    edilen senaryonun pytest'e taşınmış hali)."""
    p1 = add_policy("default_duration_minutes", "45 dk", {"default_duration_minutes": 45}, event_type="meeting")

    conflict = find_active_conflicting_policy("default_duration_minutes", event_type="meeting")
    assert conflict is not None and conflict.policy_id == p1.policy_id

    p2 = add_policy("default_duration_minutes", "90 dk", {"default_duration_minutes": 90}, event_type="meeting")
    deactivate_policy(conflict)

    active = get_active_policies()
    assert len(active) == 1
    assert active[0].policy_id == p2.policy_id
    assert active[0].structured_action == {"default_duration_minutes": 90}


# --- Kurallarım ekranı için eklenen fonksiyonlar (bkz. plan Faz 7) ---


def test_list_policies_default_excludes_inactive(temp_db):
    p1 = add_policy("importance", "kural 1", {"importance": "high"}, event_type="meeting")
    deactivate_policy(p1)
    add_policy("default_duration_minutes", "kural 2", {"default_duration_minutes": 30}, event_type="exam")

    assert len(list_policies()) == 1
    assert len(list_policies(include_inactive=True)) == 2


def test_get_active_policies_delegates_to_list_policies(temp_db):
    add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    assert [p.policy_id for p in get_active_policies()] == [p.policy_id for p in list_policies()]


def test_get_policy_by_id(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    assert get_policy(p.policy_id).policy_id == p.policy_id
    assert get_policy("olmayan-id") is None


def test_count_active_policies(temp_db):
    assert count_active_policies() == 0
    p1 = add_policy("importance", "kural 1", {"importance": "high"}, event_type="meeting")
    add_policy("default_duration_minutes", "kural 2", {"default_duration_minutes": 30}, event_type="exam")
    assert count_active_policies() == 2
    deactivate_policy(p1)
    assert count_active_policies() == 1


def test_deactivate_policy_by_id_success(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    assert deactivate_policy_by_id(p.policy_id) is True
    assert get_policy(p.policy_id).active is False


def test_deactivate_policy_by_id_unknown_returns_false(temp_db):
    assert deactivate_policy_by_id("olmayan-id") is False


def test_deactivate_policy_by_id_already_inactive_returns_false(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    deactivate_policy_by_id(p.policy_id)
    assert deactivate_policy_by_id(p.policy_id) is False


def test_reactivate_policy_success(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    deactivate_policy_by_id(p.policy_id)

    reactivated = reactivate_policy(p.policy_id)
    assert reactivated is not None
    assert reactivated.active is True
    assert reactivated.version == p.version + 1


def test_reactivate_policy_unknown_returns_none(temp_db):
    assert reactivate_policy("olmayan-id") is None


def test_reactivate_policy_already_active_returns_none(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    assert reactivate_policy(p.policy_id) is None


def test_reactivate_policy_blocked_by_conflicting_active_policy(temp_db):
    p1 = add_policy("importance", "eski kural", {"importance": "high"}, event_type="meeting")
    deactivate_policy_by_id(p1.policy_id)
    # p1 pasifken aynı category+event_type için YENİ bir aktif politika oluştu.
    add_policy("importance", "yeni kural", {"importance": "low"}, event_type="meeting")

    assert reactivate_policy(p1.policy_id) is None
    assert get_policy(p1.policy_id).active is False


def test_list_policy_versions(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    deactivate_policy(p)
    versions = list_policy_versions(p.policy_id)
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["snapshot"]["natural_language_rule"] == "kural"


def test_list_policy_versions_empty_for_never_versioned_policy(temp_db):
    p = add_policy("importance", "kural", {"importance": "high"}, event_type="meeting")
    assert list_policy_versions(p.policy_id) == []


# --- Per-user isolation (bkz. plan "Per-user isolation for rules, corrections, and suggestions") ---
# user_id, users(id)'ye FK verdiği için (PRAGMA foreign_keys=ON) gerçek kullanıcı
# satırları gerekiyor — bkz. test_account_registry.py'deki aynı desen.


def _two_users():
    from src.ui.auth import create_user

    return create_user("a@example.com"), create_user("b@example.com")


def test_list_policies_filters_by_user_id(temp_db):
    user_a, user_b = _two_users()
    add_policy("importance", "kullanıcı A'nın kuralı", {"importance": "high"}, event_type="meeting", user_id=user_a["id"])
    add_policy("importance", "kullanıcı B'nin kuralı", {"importance": "low"}, event_type="exam", user_id=user_b["id"])

    assert [p.natural_language_rule for p in list_policies(user_id=user_a["id"])] == ["kullanıcı A'nın kuralı"]
    assert [p.natural_language_rule for p in list_policies(user_id=user_b["id"])] == ["kullanıcı B'nin kuralı"]
    # user_id verilmezse (CLI, eski davranış) hepsi görünür.
    assert len(list_policies()) == 2


def test_get_active_policies_filters_by_user_id(temp_db):
    user_a, user_b = _two_users()
    add_policy("importance", "A", {"importance": "high"}, event_type="meeting", user_id=user_a["id"])
    add_policy("importance", "B", {"importance": "low"}, event_type="exam", user_id=user_b["id"])

    assert len(get_active_policies(user_id=user_a["id"])) == 1
    assert len(get_active_policies(user_id=user_b["id"])) == 1


def test_find_active_conflicting_policy_scoped_to_user(temp_db):
    user_a, user_b = _two_users()
    add_policy("default_duration_minutes", "A'nın kuralı", {"default_duration_minutes": 45}, event_type="meeting", user_id=user_a["id"])

    # Aynı category+kapsam ama FARKLI kullanıcı -> çelişki YOK (izole).
    assert find_active_conflicting_policy("default_duration_minutes", event_type="meeting", user_id=user_b["id"]) is None
    # Aynı kullanıcı -> çelişki var.
    conflict = find_active_conflicting_policy("default_duration_minutes", event_type="meeting", user_id=user_a["id"])
    assert conflict is not None


def test_get_active_policies_for_sender_scoped_to_user(temp_db):
    user_a, user_b = _two_users()
    add_policy("importance", "A'nın kuralı", {"importance": "low"}, sender="alerts@linkedin.com", user_id=user_a["id"])

    assert get_active_policies_for_sender("alerts@linkedin.com", user_id=user_b["id"]) == []
    assert len(get_active_policies_for_sender("alerts@linkedin.com", user_id=user_a["id"])) == 1


def test_count_active_policies_filters_by_user_id(temp_db):
    user_a, user_b = _two_users()
    add_policy("importance", "A", {"importance": "high"}, event_type="meeting", user_id=user_a["id"])
    add_policy("importance", "B1", {"importance": "low"}, event_type="exam", user_id=user_b["id"])
    add_policy("default_duration_minutes", "B2", {"default_duration_minutes": 30}, event_type="exam", user_id=user_b["id"])

    assert count_active_policies(user_id=user_a["id"]) == 1
    assert count_active_policies(user_id=user_b["id"]) == 2
    assert count_active_policies() == 3
