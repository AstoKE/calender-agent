"""OAuth reconnects preserve legacy IDs and never overwrite a different account."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.connectors.account_registry import ensure_account_registered, get_account, list_accounts
from src.ui.app import app
from src.ui.auth import create_session, create_user
from test_oauth_routes import _FakeFlow, _FakeGmailService


@pytest.fixture(params=["google", "outlook"])
def oauth_connection(request, temp_db, tmp_path, monkeypatch):
    provider = request.param
    user = create_user("current@example.com")
    client = TestClient(app)
    client.cookies.set("session_token", create_session(user["id"]))
    cache = {}
    monkeypatch.setattr(app.state, "calendar_connectors", cache, raising=False)
    monkeypatch.setattr("src.connectors.google_auth.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.connectors.microsoft_auth.DATA_DIR", tmp_path)

    if provider == "google":
        monkeypatch.setattr("src.ui.oauth_routes.Flow", _FakeFlow)
        monkeypatch.setattr(_FakeFlow, "fetch_token_error", None)
        monkeypatch.setattr(_FakeGmailService, "email", "ali@second.example")
        monkeypatch.setattr("src.ui.oauth_routes.build", lambda *args, **kwargs: _FakeGmailService())
        client.cookies.set("oauth_state", "state")
        url = "/hesaplar/oauth/geri-don?code=fake&state=state"
        prefix = "google_token_"
        new_token = '{"token": "fake-token"}'
    else:
        ms_app = Mock()
        ms_app.acquire_token_by_auth_code_flow.return_value = {"access_token": "fake-token"}
        ms_app.token_cache.serialize.return_value = "FAKE_MICROSOFT_CACHE"
        monkeypatch.setattr("src.ui.outlook_oauth_routes.msal.PublicClientApplication", Mock(return_value=ms_app))
        monkeypatch.setattr("src.ui.outlook_oauth_routes.ms_client_id", lambda: "fake-client")
        profile = Mock()
        profile.json.return_value = {"mail": "ali@second.example"}
        monkeypatch.setattr("src.ui.outlook_oauth_routes.requests.get", Mock(return_value=profile))
        client.cookies.set("outlook_oauth_flow", json.dumps({"state": "state"}))
        url = "/hesap-ekle-outlook/callback?code=fake&state=state"
        prefix = "ms_token_"
        new_token = "FAKE_MICROSOFT_CACHE"

    yield client, user, provider, url, lambda account_id: tmp_path / f"{prefix}{account_id}.json", new_token, cache
    client.close()


def test_oauth_same_local_part_keeps_other_account_and_token_intact(oauth_connection):
    client, user, provider, url, token_path, new_token, cache = oauth_connection
    previous_owner = create_user("previous@example.com")
    old_id = "ali" if provider == "google" else "outlook_ali"
    ensure_account_registered(old_id, provider, "ali@first.example", user_id=previous_owner["id"])
    token_path(old_id).write_text("PREVIOUS_TOKEN", encoding="utf-8")
    cache[old_id] = object()

    response = client.get(url, follow_redirects=False)

    assert response.headers["location"] == "/hesaplar?hesap_eklendi=1"
    new_id = response.cookies["active_account"]
    assert new_id != old_id
    assert get_account(old_id)["user_id"] == previous_owner["id"]
    assert get_account(new_id)["user_id"] == user["id"]
    assert get_account(new_id)["email"] == "ali@second.example"
    assert token_path(old_id).read_text(encoding="utf-8") == "PREVIOUS_TOKEN"
    assert token_path(new_id).read_text(encoding="utf-8") == new_token
    assert old_id in cache


def test_oauth_reconnect_keeps_legacy_token_path_and_refreshes_cache(oauth_connection):
    client, user, provider, url, token_path, new_token, cache = oauth_connection
    old_id = "custom-legacy-id"
    ensure_account_registered(old_id, provider, "ali@second.example", user_id=user["id"])
    token_path(old_id).write_text("PREVIOUS_TOKEN", encoding="utf-8")
    cache[old_id] = object()

    response = client.get(url, follow_redirects=False)

    assert response.headers["location"] == "/hesaplar?hesap_eklendi=1"
    assert response.cookies["active_account"] == old_id
    assert len(list_accounts()) == 1
    assert token_path(old_id).read_text(encoding="utf-8") == new_token
    assert old_id not in cache


def test_oauth_ambiguous_legacy_rows_do_not_overwrite_tokens(oauth_connection):
    client, user, provider, url, token_path, new_token, cache = oauth_connection
    for old_id in ["duplicate-a", "duplicate-b"]:
        ensure_account_registered(old_id, provider, "ali@second.example")
        token_path(old_id).write_text("PREVIOUS_TOKEN", encoding="utf-8")

    response = client.get(url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?oauth_hata=basarisiz"
    assert "active_account" not in response.cookies
    for old_id in ["duplicate-a", "duplicate-b"]:
        assert token_path(old_id).read_text(encoding="utf-8") == "PREVIOUS_TOKEN"
        assert get_account(old_id)["user_id"] is None
    assert len(list_accounts()) == 2
