from src.core.models import PolicyScope, PolicySource
from src.policies.store import (
    add_policy,
    deactivate_policy,
    find_active_conflicting_policy,
    get_active_policies,
    get_active_policies_for_sender,
    normalize_sender,
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
