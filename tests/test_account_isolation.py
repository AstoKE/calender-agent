"""Unowned local data must not become visible through web sign-in."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.candidates.store import save_new_candidate
from src.connectors.account_registry import ensure_account_registered, get_account, list_accounts
from src.memory.correction_memory import save_user_correction
from src.policies.store import add_policy
from src.storage.db import get_connection
from src.storage.preferences import set_preference
from src.ui.app import app
from src.ui.auth import create_session, create_user
from test_candidate_store import _candidate, _insert_account_and_email
from test_oauth_routes import _FakeFlow, _FakeGmailService


@pytest.fixture
def legacy_data(temp_db, monkeypatch):
    email_id = _insert_account_and_email("legacy-account", subject="LEGACY_MAIL_PRIVATE")
    candidate = _candidate(title="LEGACY_EVENT_PRIVATE")
    save_new_candidate(candidate, source_email_row_id=email_id)
    add_policy("importance", "LEGACY_RULE_PRIVATE", {"importance": "high"})
    set_preference("calendar.master_account_id", "legacy-account")
    save_user_correction(candidate, "LEGACY_CORRECTION_PRIVATE")
    monkeypatch.setattr("src.ui.routes.get_calendar_or_none", lambda *args: None)
    return candidate


@pytest.fixture
def signed_in_client(legacy_data):
    user = create_user("current@example.com")
    client = TestClient(app)
    client.cookies.set("session_token", create_session(user["id"]))
    client.test_user = user
    client.headers["sec-fetch-site"] = "same-origin"  # bkz. conftest.py::login_test_client (AUTH-03)
    yield client
    client.close()


def test_scoped_account_list_excludes_unowned_and_other_users(legacy_data):
    user = create_user("current@example.com")
    other = create_user("other@example.com")
    ensure_account_registered("mine", "google", "mine@example.com", user_id=user["id"])
    ensure_account_registered("theirs", "google", "theirs@example.com", user_id=other["id"])

    assert [a["id"] for a in list_accounts(user_id=user["id"])] == ["mine"]
    assert [a["id"] for a in list_accounts(user_id=other["id"])] == ["theirs"]
    assert {a["id"] for a in list_accounts()} == {"mine", "theirs", "legacy-account"}


@pytest.mark.parametrize("path", ["/anasayfa", "/oneriler", "/hesaplar", "/kurallarim", "/duzeltmelerim", "/ayarlar"])
def test_web_pages_and_notification_shell_hide_unowned_data(signed_in_client, path):
    response = signed_in_client.get(path)

    assert response.status_code == 200
    for private_text in [
        "legacy-account@example.com", "LEGACY_MAIL_PRIVATE", "LEGACY_EVENT_PRIVATE",
        "LEGACY_RULE_PRIVATE", "LEGACY_CORRECTION_PRIVATE",
    ]:
        assert private_text not in response.text
    assert get_account("legacy-account")["user_id"] is None


def test_unowned_account_cannot_be_selected_or_restored_by_cookie(signed_in_client):
    response = signed_in_client.post(
        "/hesap-sec", data={"account_id": "legacy-account"}, follow_redirects=False
    )
    assert "active_account" not in response.cookies

    signed_in_client.cookies.set("active_account", "legacy-account")
    response = signed_in_client.get("/oneriler")
    assert response.status_code == 200
    assert "LEGACY_EVENT_PRIVATE" not in response.text


@pytest.mark.parametrize("provider", ["google", "outlook"])
@pytest.mark.parametrize("intent", ["giris", "bagla"])
def test_oauth_claims_only_the_verified_account(legacy_data, monkeypatch, provider, intent):
    if provider == "google":
        account_id = "verified"
        monkeypatch.setattr(_FakeFlow, "fetch_token_error", None)
        monkeypatch.setattr(_FakeGmailService, "email", "verified@example.com")
        monkeypatch.setattr("src.ui.oauth_routes.Flow", _FakeFlow)
        monkeypatch.setattr("src.ui.oauth_routes.build", lambda *args, **kwargs: _FakeGmailService())
        monkeypatch.setattr("src.ui.oauth_routes.save_credentials_for_account", Mock())
        url = "/hesaplar/oauth/geri-don?code=fake&state=fake-state"
        cookies = {"oauth_state": "fake-state", "oauth_intent": intent}
    else:
        account_id = "outlook_verified"
        ms_app = Mock()
        ms_app.acquire_token_by_auth_code_flow.return_value = {"access_token": "fake-token"}
        monkeypatch.setattr("src.ui.outlook_oauth_routes.msal.PublicClientApplication", Mock(return_value=ms_app))
        monkeypatch.setattr("src.ui.outlook_oauth_routes.ms_client_id", lambda: "fake-client")
        profile = Mock()
        profile.json.return_value = {"mail": "verified@example.com"}
        monkeypatch.setattr("src.ui.outlook_oauth_routes.requests.get", Mock(return_value=profile))
        monkeypatch.setattr("src.ui.outlook_oauth_routes.save_ms_token_cache", Mock())
        url = "/hesap-ekle-outlook/callback?code=fake&state=fake-state"
        # Cookie names mirror the actual Outlook flow, independent of Google.
        from src.ui.outlook_oauth_routes import OAUTH_FLOW_COOKIE, OAUTH_INTENT_COOKIE
        cookies = {OAUTH_FLOW_COOKIE: json.dumps({"state": "fake-state"}), OAUTH_INTENT_COOKIE: intent}

    ensure_account_registered(account_id, provider, "verified@example.com")
    other = create_user("other@example.com")
    ensure_account_registered("already-owned", "google", "other@example.com", user_id=other["id"])
    client = TestClient(app)
    if intent == "bagla":
        user = create_user("current@example.com")
        cookies["session_token"] = create_session(user["id"])
    client.cookies.update(cookies)

    try:
        response = client.get(url, follow_redirects=False)
    finally:
        client.close()

    assert response.status_code == 303
    assert response.headers["location"] == ("/anasayfa" if intent == "giris" else "/hesaplar?hesap_eklendi=1")
    assert get_account(account_id)["user_id"] is not None
    assert get_account("legacy-account")["user_id"] is None
    assert get_account("already-owned")["user_id"] == other["id"]
    with get_connection() as conn:
        for table in ["user_preferences", "personal_policies", "user_corrections"]:
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL").fetchone()[0] == 1
