"""Account registration must not confuse addresses, providers or legacy IDs."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.candidates.store import get_pending_candidate, save_new_candidate
from src.connectors.account_registry import (
    AccountIdentityConflict, ensure_account_registered, get_account, list_accounts, register_account,
)
from src.storage.db import get_connection
from src.storage.preferences import get_preference, set_preference
from src.ui.auth import create_user
from test_candidate_store import _candidate, _insert_account_and_email


@pytest.mark.parametrize("provider", ["google", "outlook"])
def test_same_local_part_different_domains_remain_separate(temp_db, provider):
    user = create_user("owner@example.com")
    first = register_account(provider, "ali@company-a.example", user_id=user["id"])
    second = register_account(provider, "ali@company-b.example", user_id=user["id"])

    assert first != second
    assert get_account(first)["email"] == "ali@company-a.example"
    assert get_account(second)["email"] == "ali@company-b.example"
    assert len(list_accounts(user["id"])) == 2


def test_same_address_on_different_providers_remains_separate(temp_db):
    google_id = register_account("google", "same@example.com")
    outlook_id = register_account("outlook", "same@example.com")
    assert google_id != outlook_id
    assert get_account(google_id)["provider"] == "google"
    assert get_account(outlook_id)["provider"] == "outlook"


@pytest.mark.parametrize("provider", ["google", "outlook"])
def test_reconnect_reuses_arbitrary_legacy_id_and_preserves_related_data(temp_db, provider):
    user = create_user("owner@example.com")
    ensure_account_registered("old-custom-id", provider, "Ali@Example.com")
    email_id = _insert_account_and_email(account_id="old-custom-id")
    candidate = _candidate()
    save_new_candidate(candidate, source_email_row_id=email_id)
    set_preference("calendar.master_account_id", "old-custom-id", user_id=user["id"])

    account_id = register_account(provider, " ALI@example.com ", user_id=user["id"])

    assert account_id == "old-custom-id"
    assert get_pending_candidate(candidate.candidate_id, user_id=user["id"]) is not None
    assert get_preference("calendar.master_account_id", user_id=user["id"]) == account_id
    assert len(list_accounts()) == 1


def test_new_address_cannot_take_over_legacy_local_part(temp_db):
    first_owner = create_user("first@example.com")
    second_owner = create_user("second@example.com")
    ensure_account_registered("ali", "google", "ali@company-a.example", user_id=first_owner["id"])

    new_id = register_account("google", "ali@company-b.example", user_id=second_owner["id"])

    assert new_id != "ali"
    assert get_account("ali")["user_id"] == first_owner["id"]
    assert get_account(new_id)["user_id"] == second_owner["id"]


@pytest.mark.parametrize("provider,email", [
    ("google", "different@example.com"), ("outlook", "same@example.com"),
])
def test_explicit_id_identity_mismatch_does_not_transfer_owner(temp_db, provider, email):
    first = create_user("first@example.com")
    second = create_user("second@example.com")
    ensure_account_registered("legacy", "google", "same@example.com", user_id=first["id"])
    before = get_account("legacy")

    with pytest.raises(AccountIdentityConflict):
        ensure_account_registered("legacy", provider, email, user_id=second["id"])

    assert get_account("legacy") == before


def test_ambiguous_legacy_identity_is_not_merged_or_claimed(temp_db):
    user = create_user("owner@example.com")
    ensure_account_registered("old-a", "google", "same@example.com")
    ensure_account_registered("old-b", "google", "same@example.com")
    before = list_accounts()

    with pytest.raises(AccountIdentityConflict):
        register_account("google", "same@example.com", user_id=user["id"])

    assert list_accounts() == before
    assert get_account("old-a")["user_id"] is None
    assert get_account("old-b")["user_id"] is None


def test_parallel_registration_creates_one_account(temp_db):
    user = create_user("owner@example.com")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(
            lambda _: register_account("google", "same@example.com", user_id=user["id"]), range(8)
        ))
    assert len(set(ids)) == 1
    assert len(list_accounts()) == 1


def test_local_registration_does_not_clear_web_owner(temp_db):
    user = create_user("owner@example.com")
    original = register_account("google", "same@example.com", user_id=user["id"])
    assert register_account("google", "SAME@example.com") == original
    assert get_account(original)["user_id"] == user["id"]


@pytest.mark.parametrize("provider,email", [
    ("unsupported", "same@example.com"), ("google", "invalid"), ("google", "@example.com"),
])
def test_invalid_identity_does_not_create_records(temp_db, provider, email):
    with pytest.raises(ValueError):
        register_account(provider, email)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
