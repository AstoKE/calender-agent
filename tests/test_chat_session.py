"""src/ui/chat_session.py için testler — cookie kendini-onarma + çapraz-hesap
izolasyonu (bkz. plan "Web Chatbox" Faz 1)."""

from __future__ import annotations

from unittest.mock import Mock

from fastapi import Response

from src.connectors.account_registry import ensure_account_registered
from src.storage.db import get_connection
from src.ui.chat_session import (
    CHAT_SESSION_COOKIE,
    create_new_chat_session,
    get_chat_session,
    get_current_chat_session_id,
    get_or_create_chat_session,
    list_chat_sessions,
    switch_chat_session,
)


def _request(cookies: dict | None = None) -> Mock:
    req = Mock()
    req.cookies = cookies or {}
    return req


def _cookie_value(response: Response) -> str | None:
    set_cookie = response.headers.get("set-cookie")
    if not set_cookie:
        return None
    # 'chat_session=<uuid>; Path=/; ...' -> <uuid>
    first_pair = set_cookie.split(";", 1)[0]
    return first_pair.split("=", 1)[1]


def test_creates_new_session_when_no_cookie(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = Response()
    session_id = get_or_create_chat_session(_request(), response, "acc1")

    assert session_id
    assert _cookie_value(response) == session_id
    row = get_chat_session(session_id)
    assert row["account_id"] == "acc1"
    assert row["flow"] is None
    assert row["state_json"] == "{}"


def test_reuses_existing_session_for_same_account(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response1 = Response()
    first_id = get_or_create_chat_session(_request(), response1, "acc1")

    response2 = Response()
    second_id = get_or_create_chat_session(_request({CHAT_SESSION_COOKIE: first_id}), response2, "acc1")

    assert second_id == first_id
    assert _cookie_value(response2) is None  # zaten geçerli — yeniden yazılmadı


def test_stale_cookie_self_heals_with_new_session(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = Response()
    session_id = get_or_create_chat_session(_request({CHAT_SESSION_COOKIE: "olmayan-id"}), response, "acc1")

    assert session_id != "olmayan-id"
    assert _cookie_value(response) == session_id


def test_cross_account_session_is_not_reused(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")

    response1 = Response()
    acc1_session = get_or_create_chat_session(_request(), response1, "acc1")

    # acc1'in cookie'siyle acc2 için oturum istenirse (kullanıcı hesap
    # değiştirmiş) yeni bir oturum açılmalı, acc1'inki asla döndürülmemeli.
    response2 = Response()
    acc2_session = get_or_create_chat_session(
        _request({CHAT_SESSION_COOKIE: acc1_session}), response2, "acc2"
    )

    assert acc2_session != acc1_session
    assert get_chat_session(acc2_session)["account_id"] == "acc2"
    assert get_chat_session(acc1_session)["account_id"] == "acc1"  # dokunulmamış


def test_create_new_chat_session_ignores_existing_cookie_and_keeps_old_session(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response1 = Response()
    old_session = get_or_create_chat_session(_request(), response1, "acc1")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, text, created_at) VALUES (?,?,?,?)",
            (old_session, "user", "eski mesaj", "2026-01-01T00:00:00+00:00"),
        )

    response2 = Response()
    new_session = create_new_chat_session(response2, "acc1")

    assert new_session != old_session
    assert _cookie_value(response2) == new_session
    # Eski oturum ve mesajı DOKUNULMADAN duruyor.
    assert get_chat_session(old_session) is not None
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (old_session,)
        ).fetchone()[0]
    assert count == 1


def test_switch_chat_session_sets_cookie_when_owned_by_account(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response1 = Response()
    session_id = get_or_create_chat_session(_request(), response1, "acc1")

    response2 = Response()
    ok = switch_chat_session(response2, "acc1", session_id)

    assert ok is True
    assert _cookie_value(response2) == session_id


def test_switch_chat_session_rejects_session_from_another_account(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    response1 = Response()
    acc1_session = get_or_create_chat_session(_request(), response1, "acc1")

    response2 = Response()
    ok = switch_chat_session(response2, "acc2", acc1_session)

    assert ok is False
    assert _cookie_value(response2) is None


def test_switch_chat_session_rejects_unknown_session(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = Response()
    ok = switch_chat_session(response, "acc1", "olmayan-id")
    assert ok is False


def test_list_chat_sessions_excludes_empty_sessions_and_orders_by_recency(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = Response()
    empty_session = get_or_create_chat_session(_request(), response, "acc1")  # hiç mesaj yok

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, account_id, flow, step, state_json, created_at, updated_at) "
            "VALUES ('s-old', 'acc1', NULL, NULL, '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO chat_sessions (session_id, account_id, flow, step, state_json, created_at, updated_at) "
            "VALUES ('s-new', 'acc1', NULL, NULL, '{}', '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, text, created_at) VALUES "
            "('s-old', 'user', 'eski soru', '2026-01-01T00:00:01+00:00')"
        )
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, text, created_at) VALUES "
            "('s-new', 'user', 'yeni soru', '2026-01-02T00:00:01+00:00'), "
            "('s-new', 'assistant', 'yanıt', '2026-01-02T00:00:02+00:00')"
        )

    sessions = list_chat_sessions("acc1")

    session_ids = [s["session_id"] for s in sessions]
    assert empty_session not in session_ids  # mesajsız oturum listede yok
    assert session_ids == ["s-new", "s-old"]  # en yeni önce
    new_entry = next(s for s in sessions if s["session_id"] == "s-new")
    assert new_entry["preview"] == "yeni soru"
    assert new_entry["message_count"] == 2


def test_get_chat_session_unknown_id_returns_none(temp_db):
    assert get_chat_session("olmayan-id") is None


def test_get_current_chat_session_id_no_cookie_returns_none_without_creating(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    assert get_current_chat_session_id(_request(), "acc1") is None
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert count == 0  # salt okunur — hiçbir satır oluşturmamalı


def test_get_current_chat_session_id_returns_existing(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = Response()
    session_id = get_or_create_chat_session(_request(), response, "acc1")

    found = get_current_chat_session_id(_request({CHAT_SESSION_COOKIE: session_id}), "acc1")
    assert found == session_id


def test_get_current_chat_session_id_wrong_account_returns_none(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    ensure_account_registered("acc2", provider="google", email="b@example.com")
    response = Response()
    session_id = get_or_create_chat_session(_request(), response, "acc1")

    found = get_current_chat_session_id(_request({CHAT_SESSION_COOKIE: session_id}), "acc2")
    assert found is None
