"""Gmail connector (bkz. docs/architecture-plan.md §13).

Yalnızca okuma izni ister (``gmail.readonly``) — read/write ayrıştırması
ilkesi gereği (§13) bu connector hiçbir zaman mail göndermez/değiştirmez.
"""

from __future__ import annotations

import base64
import html as html_lib
import re
from datetime import datetime, timezone

from googleapiclient.discovery import build

from src.connectors.base import EmailConnector
from src.connectors.google_auth import GOOGLE_ACCOUNT_SCOPES, get_google_credentials
from src.core.models import EmailProvider, UnifiedEmail

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw_html: str) -> str:
    return html_lib.unescape(_TAG_RE.sub(" ", raw_html)).strip()


def _decode_part_data(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _extract_body_text(payload: dict) -> str:
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})

    if mime_type == "text/plain" and body.get("data"):
        return _decode_part_data(body["data"])

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode_part_data(part["body"]["data"])
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return _strip_html(_decode_part_data(part["body"]["data"]))
    for part in parts:
        if part.get("parts"):
            nested = _extract_body_text(part)
            if nested:
                return nested

    if mime_type == "text/html" and body.get("data"):
        return _strip_html(_decode_part_data(body["data"]))

    return ""


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


class GmailConnector(EmailConnector):
    def __init__(self, account_id: str):
        self.account_id = account_id
        creds = get_google_credentials(GOOGLE_ACCOUNT_SCOPES, account_id)
        self._service = build("gmail", "v1", credentials=creds)

    def list_new_messages(self, since_cursor: str | None) -> tuple[list[UnifiedEmail], str]:
        if since_cursor is None:
            return self._initial_sync()
        return self._incremental_sync(since_cursor)

    def _initial_sync(self, max_results: int = 25) -> tuple[list[UnifiedEmail], str]:
        profile = self._service.users().getProfile(userId="me").execute()
        latest_history_id = profile["historyId"]

        list_resp = self._service.users().messages().list(userId="me", maxResults=max_results).execute()
        message_ids = [m["id"] for m in list_resp.get("messages", [])]
        messages = [self.get_message(mid) for mid in message_ids]
        return messages, latest_history_id

    def _incremental_sync(self, since_history_id: str) -> tuple[list[UnifiedEmail], str]:
        new_message_ids: set[str] = set()
        latest_history_id = since_history_id
        page_token = None

        while True:
            resp = (
                self._service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=since_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                )
                .execute()
            )

            for record in resp.get("history", []):
                for added in record.get("messagesAdded", []):
                    new_message_ids.add(added["message"]["id"])

            latest_history_id = resp.get("historyId", latest_history_id)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        messages = [self.get_message(mid) for mid in new_message_ids]
        return messages, latest_history_id

    def get_message(self, message_id: str) -> UnifiedEmail:
        msg = self._service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
        recipients_raw = _header(headers, "To")
        recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

        return UnifiedEmail(
            provider=EmailProvider.GMAIL,
            account_id=self.account_id,
            message_id=msg["id"],
            thread_id=msg["threadId"],
            subject=_header(headers, "Subject"),
            sender=_header(headers, "From"),
            recipients=recipients,
            received_at=received_at,
            body_text=_extract_body_text(payload),
            source_url_or_reference=f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}",
        )
