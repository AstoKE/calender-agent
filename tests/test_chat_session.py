"""src/ui/chat_session.py için testler — cookie kendini-onarma + çapraz-hesap
izolasyonu (bkz. plan "Web Chatbox" Faz 1)."""

from __future__ import annotations

from unittest.mock import Mock

from fastapi import Response

from src.connectors.account_registry import ensure_account_registered
from src.storage.db import get_connection
from src.ui.chat_session import (
    CHAT_SESSION_COOKIE,
    get_chat_session,
    get_current_chat_session_id,
    get_or_create_chat_session,
    reset_chat_session,
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


def test_reset_chat_session_clears_flow_but_keeps_session_id(temp_db):
    ensure_account_registered("acc1", provider="google", email="a@example.com")
    response = Response()
    session_id = get_or_create_chat_session(_request(), response, "acc1")

    with get_connection() as conn:
        conn.execute(
            "UPDATE chat_sessions SET flow = 'create_event', step = 'ask_title', state_json = '{\"x\": 1}' "
            "WHERE session_id = ?",
            (session_id,),
        )

    reset_chat_session(session_id)

    row = get_chat_session(session_id)
    assert row["session_id"] == session_id
    assert row["flow"] is None
    assert row["step"] is None
    assert row["state_json"] == "{}"


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
