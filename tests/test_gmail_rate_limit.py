"""Canlı testte bulunan gerçek hata: büyük bir gelen kutusunda (birden fazla
hesap taranırken) Gmail'in dakika başı API kotasına takılınca, o ana kadar
başarıyla çekilmiş mesajlar da dahil TÜM tarama turu çöküyor ve kullanıcıya
ham bir `HttpError` metni gösteriliyordu — bkz. src/connectors/gmail.py
(`_fetch_messages_resiliently`/`_is_rate_limit_error`) ve
src/services/scan_inbox.py (`_friendly_scan_error`). Gerçek Gmail API'ye
hiç dokunulmuyor."""

from __future__ import annotations

import json

from googleapiclient.errors import HttpError

from src.connectors.gmail import GmailConnector, _is_rate_limit_error
from src.services.scan_inbox import _friendly_scan_error


class _FakeHttpResponse:
    def __init__(self, status: int):
        self.status = status
        self.reason = ""


def _http_error(status: int, reason: str, message: str = "Quota exceeded") -> HttpError:
    content = json.dumps({"error": {"message": message, "errors": [{"reason": reason}]}}).encode()
    return HttpError(_FakeHttpResponse(status), content, uri="https://gmail.googleapis.com/fake")


def _connector() -> GmailConnector:
    return object.__new__(GmailConnector)  # __init__ atlanır — gerçek OAuth/build hiç çalışmaz


def test_is_rate_limit_error_recognizes_403_rate_limit_exceeded():
    assert _is_rate_limit_error(_http_error(403, "rateLimitExceeded"))


def test_is_rate_limit_error_recognizes_429():
    assert _is_rate_limit_error(_http_error(429, "rateLimitExceeded"))


def test_is_rate_limit_error_does_not_flag_a_real_permission_error():
    assert not _is_rate_limit_error(_http_error(403, "forbidden", message="The caller does not have permission"))


def test_fetch_messages_resiliently_stops_on_rate_limit_and_keeps_partial_results():
    connector = _connector()
    connector.account_id = "acc1"
    fetched = ["m1", "m2"]

    def fake_get(message_id):
        if message_id == "m3":
            raise _http_error(403, "rateLimitExceeded")
        return message_id  # gerçek bir UnifiedEmail olması gerekmiyor, yalnızca kimlik karşılaştırması

    connector._get_message_if_exists = fake_get

    messages, complete = connector._fetch_messages_resiliently(["m1", "m2", "m3", "m4", "m5"])

    assert messages == fetched
    assert complete is False


def test_fetch_messages_resiliently_reraises_non_rate_limit_errors():
    connector = _connector()

    def fake_get(message_id):
        raise _http_error(403, "forbidden", message="The caller does not have permission")

    connector._get_message_if_exists = fake_get

    try:
        connector._fetch_messages_resiliently(["m1"])
        assert False, "beklenen HttpError yükselmedi"
    except HttpError:
        pass


def test_incremental_sync_does_not_advance_cursor_when_incomplete(monkeypatch):
    connector = _connector()
    connector.account_id = "acc1"
    connector._service = None  # history().list() çağrılmayacak şekilde monkeypatch'leniyor

    class _FakeHistoryList:
        def execute(self):
            return {
                "history": [{"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]}],
                "historyId": "999",
            }

    class _FakeHistory:
        def list(self, **kwargs):
            return _FakeHistoryList()

    class _FakeUsers:
        def history(self):
            return _FakeHistory()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    connector._service = _FakeService()
    connector._fetch_messages_resiliently = lambda message_ids: (["m1"], False)  # m2 kotaya takıldı

    messages, new_cursor = connector._incremental_sync("100")

    assert messages == ["m1"]
    assert new_cursor == "100"  # "999" DEĞİL — ilerletilmedi


def test_incremental_sync_advances_cursor_when_complete(monkeypatch):
    connector = _connector()
    connector.account_id = "acc1"

    class _FakeHistoryList:
        def execute(self):
            return {"history": [{"messagesAdded": [{"message": {"id": "m1"}}]}], "historyId": "999"}

    class _FakeHistory:
        def list(self, **kwargs):
            return _FakeHistoryList()

    class _FakeUsers:
        def history(self):
            return _FakeHistory()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    connector._service = _FakeService()
    connector._fetch_messages_resiliently = lambda message_ids: (["m1"], True)

    messages, new_cursor = connector._incremental_sync("100")

    assert new_cursor == "999"


def test_friendly_scan_error_shortens_rate_limit_dump():
    exc = _http_error(403, "rateLimitExceeded", message="Quota exceeded for quota metric 'Total Query Cost'")
    friendly = _friendly_scan_error(exc)
    assert "kota" in friendly.lower()
    assert "Quota exceeded for quota metric" not in friendly


def test_friendly_scan_error_passes_through_unrecognized_errors():
    friendly = _friendly_scan_error(RuntimeError("beklenmedik bir hata"))
    assert friendly == "beklenmedik bir hata"


def test_friendly_scan_error_truncates_very_long_unrecognized_errors():
    friendly = _friendly_scan_error(RuntimeError("x" * 500))
    assert len(friendly) <= 301
    assert friendly.endswith("…")
