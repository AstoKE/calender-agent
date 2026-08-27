"""src/connectors/outlook.py için deterministik testler — gerçek Microsoft
Graph API'sine hiç dokunulmaz, `requests.request` sahte bir sürümle
monkeypatch'lenir. `OutlookConnector(access_token=...)` verilerek gerçek
OAuth akışı (get_ms_token) hiç çalıştırılmaz."""

from __future__ import annotations

from src.connectors import outlook as outlook_module
from src.connectors.outlook import OutlookConnector


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data if json_data is not None else {}
        self.content = b"x" if json_data is not None else b""
        self.text = str(json_data)

    def json(self):
        return self._json_data


def _connector(fake_request):
    outlook_module.requests.request = fake_request  # type: ignore[attr-defined]
    return OutlookConnector(account_id="acc1", access_token="FAKE_TOKEN")


def test_initial_sync_paginates_sorts_and_truncates(monkeypatch):
    # İki sayfalık bir delta yanıtı: 1. sayfa nextLink taşır, 2. sayfa
    # deltaLink ile biter. 3 mesaj dönüyor, en yeni receivedDateTime'a göre
    # sıralanıp ilk 2'si (max_results=2) tutulmalı.
    page1 = {
        "value": [{"id": "m1", "receivedDateTime": "2026-08-20T10:00:00Z"}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
    }
    page2 = {
        "value": [
            {"id": "m2", "receivedDateTime": "2026-08-27T10:00:00Z"},
            {"id": "m3", "receivedDateTime": "2026-08-25T10:00:00Z"},
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta-link-1",
    }
    message_detail = {
        "id": "PLACEHOLDER", "conversationId": "conv1", "subject": "Toplantı",
        "from": {"emailAddress": {"address": "alerts@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
        "receivedDateTime": "2026-08-27T10:00:00Z",
        "body": {"content": "Merhaba"}, "hasAttachments": False, "webLink": "https://outlook.com/m",
    }

    # URL'e göre kesin eşleme yapan bir sahte istemci.
    responses = {
        "https://graph.microsoft.com/v1.0/me/mailFolders('inbox')/messages/delta": page1,
        "https://graph.microsoft.com/v1.0/next-page": page2,
    }

    def fake_request(method, url, *, params=None, json=None, headers=None, timeout=None):
        if url in responses:
            return _FakeResponse(200, responses[url])
        # get_message çağrıları /me/messages/{id} şeklinde gelir.
        detail = dict(message_detail)
        detail["id"] = url.rsplit("/", 1)[-1]
        return _FakeResponse(200, detail)

    connector = _connector(fake_request)
    messages, delta_link = connector._initial_sync(max_results=2)

    assert delta_link == "https://graph.microsoft.com/v1.0/delta-link-1"
    assert [m.message_id for m in messages] == ["m2", "m3"]  # en yeni ikisi, sıralı
    assert messages[0].provider == "outlook"
    assert messages[0].thread_id == "conv1"
    assert messages[0].sender == "alerts@example.com"
    assert messages[0].source_url_or_reference == "https://outlook.com/m"


def test_get_message_maps_fields_and_fetches_attachments(monkeypatch):
    message_detail = {
        "id": "m1", "conversationId": "conv1", "subject": "Bilet",
        "from": {"emailAddress": {"address": "biletix@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
        "receivedDateTime": "2026-08-27T10:00:00Z",
        "body": {"content": "Konser biletiniz ekte"}, "hasAttachments": True,
        "webLink": "https://outlook.com/m1",
    }
    attachments_resp = {
        "value": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "att1", "name": "bilet.pdf",
             "contentType": "application/pdf", "size": 1234},
            {"@odata.type": "#microsoft.graph.itemAttachment", "id": "att2", "name": "forwarded-mail"},
        ]
    }

    def fake_request(method, url, *, params=None, json=None, headers=None, timeout=None):
        if url.endswith("/me/messages/m1"):
            assert headers.get("Prefer") == 'outlook.body-content-type="text"'
            return _FakeResponse(200, message_detail)
        if url.endswith("/me/messages/m1/attachments"):
            return _FakeResponse(200, attachments_resp)
        raise AssertionError(f"beklenmeyen URL: {url}")

    connector = _connector(fake_request)
    email = connector.get_message("m1")

    assert email.subject == "Bilet"
    assert email.sender == "biletix@example.com"
    assert email.recipients == ["me@example.com"]
    assert email.body_text == "Konser biletiniz ekte"
    assert len(email.attachments) == 1  # itemAttachment elendi, yalnızca fileAttachment kaldı
    assert email.attachments[0].filename == "bilet.pdf"
    assert email.attachments[0].attachment_id == "att1"


def test_get_message_if_exists_swallows_404(monkeypatch):
    def fake_request(method, url, *, params=None, json=None, headers=None, timeout=None):
        return _FakeResponse(404, {"error": "not found"})

    connector = _connector(fake_request)
    assert connector._get_message_if_exists("gone") is None


def test_download_attachment_decodes_base64(monkeypatch):
    import base64

    raw = b"FAKE PDF BYTES"

    def fake_request(method, url, *, params=None, json=None, headers=None, timeout=None):
        assert url.endswith("/me/messages/m1/attachments/att1")
        return _FakeResponse(200, {"contentBytes": base64.b64encode(raw).decode("ascii")})

    connector = _connector(fake_request)
    result = connector.download_attachment("m1", "att1")

    assert result == raw
