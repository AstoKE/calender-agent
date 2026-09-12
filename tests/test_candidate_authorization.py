"""Suggestion ID access must respect the source account's current owner."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.candidates.store import get_pending_candidate, save_new_candidate, update_candidate_fields
from src.storage.db import get_connection
from src.ui.app import app
from src.ui.auth import create_session, create_user
from test_candidate_store import _candidate, _insert_account_and_email


ACTIONS = [
    ("GET", "duzenle", {}),
    ("POST", "duzenle", {"title": "Edited title"}),
    ("POST", "onayla", {}),
    ("POST", "onayla", {"force": "true"}),
    ("POST", "reddet", {"reason": "Not relevant"}),
]


@pytest.fixture
def scenario(temp_db, monkeypatch):
    owner = create_user("owner@example.com")
    other = create_user("other@example.com")
    email_id = _insert_account_and_email(account_id="owner-account")
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET user_id = ?", (owner["id"],))
    candidate = _candidate(title="Private owner event")
    save_new_candidate(candidate, source_email_row_id=email_id)
    calendar = Mock()
    calendar.get_freebusy.return_value = []
    calendar.create_event.return_value = "test-created-event"
    get_calendar = Mock(return_value=calendar)
    monkeypatch.setattr("src.ui.routes._get_calendar", get_calendar)
    # No lifespan: these routes need neither model initialization nor real OAuth.
    client = TestClient(app)
    # AUTH-03 (bkz. src/ui/security.py): CSRFGuardMiddleware artık Fetch
    # Metadata başlığı hiç yoksa isteği REDDEDİYOR — bu istemci giriş
    # yapılmadan ÖNCE de POST atıyor (bkz. test_candidate_requests_require_login),
    # bu yüzden başlık burada, _sign_in'de değil, gerçek bir tarayıcının aynı
    # sekmeden HER isteğinde (kimlik durumundan bağımsız) gönderdiği şeyi
    # taklit edecek şekilde ayarlanıyor.
    client.headers["sec-fetch-site"] = "same-origin"
    yield client, owner, other, candidate.candidate_id, get_calendar
    client.close()


def _sign_in(client, user):
    client.cookies.set("session_token", create_session(user["id"]))


def _snapshot(candidate_id):
    with get_connection() as conn:
        row = dict(conn.execute(
            "SELECT * FROM candidate_events WHERE candidate_id = ?", (candidate_id,)
        ).fetchone())
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        correction_count = conn.execute("SELECT COUNT(*) FROM user_corrections").fetchone()[0]
    return row, audit_count, correction_count


def _request(client, candidate_id, method, action, data):
    kwargs = {"data": data} if method == "POST" else {}
    return client.request(
        method, f"/oneriler/{candidate_id}/{action}", follow_redirects=False, **kwargs
    )


@pytest.mark.parametrize("method,action,data", ACTIONS)
@pytest.mark.parametrize("ownership", ["other-user", "unowned", "missing"])
def test_unauthorized_candidate_requests_have_no_side_effects(
    scenario, method, action, data, ownership
):
    client, owner, other, candidate_id, get_calendar = scenario
    _sign_in(client, other)
    if ownership == "unowned":
        with get_connection() as conn:
            conn.execute("UPDATE accounts SET user_id = NULL")
    before = _snapshot(candidate_id)
    requested_id = "nonexistent-candidate" if ownership == "missing" else candidate_id

    response = _request(client, requested_id, method, action, data)

    assert response.status_code == 404
    if ownership == "other-user":
        assert "Private owner event" not in response.text
    assert _snapshot(candidate_id) == before
    get_calendar.assert_not_called()


@pytest.mark.parametrize("method,action,data", ACTIONS)
def test_owner_can_access_and_act_on_candidate(scenario, method, action, data):
    client, owner, other, candidate_id, get_calendar = scenario
    _sign_in(client, owner)

    response = _request(client, candidate_id, method, action, data)

    assert response.status_code == (200 if method == "GET" else 303)
    row, audit_count, correction_count = _snapshot(candidate_id)
    if method == "GET":
        assert "Private owner event" in response.text
    elif action == "duzenle":
        assert row["title"] == "Edited title"
    elif action == "onayla":
        get_calendar.assert_called_once()
        get_calendar.return_value.create_event.assert_called_once()
        assert row["status"] == "ADDED_TO_CALENDAR"
        assert row["google_event_id"] == "test-created-event"
        assert audit_count == 1
    else:
        assert row["status"] == "REJECTED"
        assert audit_count == 1
        assert correction_count == 1
    if action != "onayla":
        get_calendar.assert_not_called()


@pytest.mark.parametrize("method,action,data", ACTIONS)
def test_candidate_requests_require_login(scenario, method, action, data):
    client, owner, other, candidate_id, get_calendar = scenario
    before = _snapshot(candidate_id)

    response = _request(client, candidate_id, method, action, data)

    assert response.status_code == 303
    assert response.headers["location"] == "/giris"
    assert _snapshot(candidate_id) == before
    get_calendar.assert_not_called()


@pytest.mark.parametrize("action", ["onayla", "reddet"])
def test_update_suggestion_checks_owner_before_update_or_revert(scenario, action):
    client, owner, other, candidate_id, get_calendar = scenario
    with get_connection() as conn:
        conn.execute(
            "UPDATE candidate_events SET status = 'UPDATE_SUGGESTED', "
            "google_event_id = 'existing-event', previous_snapshot = ? WHERE candidate_id = ?",
            ('{"title": "Original title"}', candidate_id),
        )
    _sign_in(client, other)
    before = _snapshot(candidate_id)

    response = _request(client, candidate_id, "POST", action, {"force": "true"})

    assert response.status_code == 404
    assert _snapshot(candidate_id) == before
    get_calendar.assert_not_called()


def test_previous_owner_loses_access_after_account_transfer(scenario):
    client, owner, other, candidate_id, get_calendar = scenario
    _sign_in(client, owner)
    assert client.get(f"/oneriler/{candidate_id}/duzenle").status_code == 200
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET user_id = ?", (other["id"],))
    before = _snapshot(candidate_id)

    response = client.post(
        f"/oneriler/{candidate_id}/duzenle", data={"title": "Stale tab edit"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert _snapshot(candidate_id) == before
    get_calendar.assert_not_called()


@pytest.mark.parametrize("unowned", [False, True])
def test_store_scope_rejects_inaccessible_reads_and_edits(scenario, unowned):
    client, owner, other, candidate_id, get_calendar = scenario
    assert get_pending_candidate(candidate_id, user_id=owner["id"]) is not None
    if unowned:
        with get_connection() as conn:
            conn.execute("UPDATE accounts SET user_id = NULL")
    before = _snapshot(candidate_id)

    assert get_pending_candidate(candidate_id, user_id=other["id"]) is None
    with pytest.raises(ValueError):
        update_candidate_fields(candidate_id, user_id=other["id"], title="Unauthorized edit")

    assert _snapshot(candidate_id) == before
    get_calendar.assert_not_called()
