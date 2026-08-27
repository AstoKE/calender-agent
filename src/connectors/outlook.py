"""Microsoft Graph tabanlı Outlook mail connector (bkz. docs/architecture-plan.md §13).

`GmailConnector`'ın Microsoft Graph karşılığı — AYNI provider-nötr
`EmailConnector` arayüzünü uygular. Yalnızca okuma izni ister
(``Mail.Read``, bkz. microsoft_auth.py) — Gmail connector'ıyla AYNI
read/write ayrıştırması ilkesi.

Gmail'in `historyId`'sinin karşılığı Graph'ta **delta query**
(`/me/mailFolders('inbox')/messages/delta`): ilk çağrıda `$deltatoken`
verilmez, sayfalama biterken dönen `@odata.deltaLink` bir sonraki
senkronizasyon için saklanır; o link'e tekrar GET atmak yalnızca o
zamandan beri eklenen/değişen/silinen mesajları döner. Gmail'in
`getProfile().historyId`'sinin aksine Graph'ın mail delta'sı "şu andan
başla" kısayolu SUNMUYOR (ilk çağrı her zaman TÜM posta kutusunu
sayfalar) — bu yüzden ilk senkronda yalnızca `id`/`receivedDateTime`
(hafif alanlar) çekilip en yeni `INITIAL_SYNC_LIMIT` kadarının TAM
içeriği ayrıca çekiliyor (Gmail'in "ilk senkronda son ~25 mesaj"
sınırıyla AYNI gerekçe/sonuç, farklı bir API kısıtı yüzünden farklı bir
yolla elde ediliyor)."""

from __future__ import annotations

import base64
from datetime import datetime

import requests

from src.connectors.base import EmailConnector
from src.connectors.microsoft_auth import MS_ACCOUNT_SCOPES, get_ms_token
from src.core.logging_config import get_logger
from src.core.models import Attachment, EmailProvider, UnifiedEmail

logger = get_logger("outlook_connector")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
INITIAL_SYNC_LIMIT = 25

_MESSAGE_SELECT = "id,conversationId,subject,from,toRecipients,receivedDateTime,body,hasAttachments,webLink"


class GraphApiError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class OutlookConnector(EmailConnector):
    def __init__(self, account_id: str, access_token: str | None = None):
        """``access_token`` verilirse OAuth akışı hiç çalıştırılmaz —
        MSCalendarConnector'daki AYNI desen (bkz. ms_calendar.py)."""
        self.account_id = account_id
        self._token = access_token if access_token is not None else get_ms_token(MS_ACCOUNT_SCOPES, account_id)

    def _request(
        self, method: str, url: str, *, params: dict | None = None, json_body: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"}
        if extra_headers:
            headers.update(extra_headers)
        resp = requests.request(method, url, params=params, json=json_body, headers=headers, timeout=30)
        if not resp.ok:
            raise GraphApiError(resp.status_code, f"Microsoft Graph API hatası ({resp.status_code}): {resp.text}")
        if not resp.content:
            return {}
        return resp.json()

    # --- Delta senkronizasyonu (Gmail'in history.list'ine karşılık) ---

    def _drain_delta(self, delta_link: str | None, select: str) -> tuple[list[dict], str]:
        """`delta_link` None ise TÜM posta kutusunu sayfalar (yalnızca ilk
        senkronda, bkz. modül docstring'i); verilirse yalnızca o zamandan
        beri değişenleri. Döner: (silinmemiş öğeler, YENİ delta link)."""
        url = delta_link or f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta"
        params: dict | None = None if delta_link else {"$select": select}
        items: list[dict] = []
        new_delta_link = delta_link or ""
        while url:
            data = self._request("GET", url, params=params)
            params = None  # @odata.nextLink/deltaLink zaten kodlanmış (bkz. ms_calendar.py'deki AYNI not)
            items.extend(item for item in data.get("value", []) if "@removed" not in item)
            url = data.get("@odata.nextLink")
            if "@odata.deltaLink" in data:
                new_delta_link = data["@odata.deltaLink"]
        return items, new_delta_link

    def list_new_messages(self, since_cursor: str | None) -> tuple[list[UnifiedEmail], str]:
        if since_cursor is None:
            return self._initial_sync()
        return self._incremental_sync(since_cursor)

    def _initial_sync(self, max_results: int = INITIAL_SYNC_LIMIT) -> tuple[list[UnifiedEmail], str]:
        items, delta_link = self._drain_delta(None, select="id,receivedDateTime")
        items.sort(key=lambda i: i.get("receivedDateTime", ""), reverse=True)
        ids = [i["id"] for i in items[:max_results]]
        messages = [m for mid in ids if (m := self._get_message_if_exists(mid)) is not None]
        return messages, delta_link

    def _incremental_sync(self, delta_link: str) -> tuple[list[UnifiedEmail], str]:
        items, new_delta_link = self._drain_delta(delta_link, select="id")
        ids = [i["id"] for i in items]
        messages = [m for mid in ids if (m := self._get_message_if_exists(mid)) is not None]
        return messages, new_delta_link

    def _get_message_if_exists(self, message_id: str) -> UnifiedEmail | None:
        """`get_message` sarmalayıcısı — Gmail connector'ındaki AYNI 404
        toleransı (delta bir mesajı "değişti" diye bildirebilir ama mesaj
        sonradan silinmiş/taşınmış olabilir)."""
        try:
            return self.get_message(message_id)
        except GraphApiError as e:
            if e.status_code == 404:
                logger.debug("Mesaj artık mevcut değil (404), atlanıyor: %s", message_id)
                return None
            raise

    def get_message(self, message_id: str) -> UnifiedEmail:
        # Prefer: outlook.body-content-type="text" — Graph'ın varsayılan HTML
        # gövdesi yerine düz metin döndürmesini ister, Gmail connector'ındaki
        # gibi elle HTML strip etmeye gerek bırakmaz.
        data = self._request(
            "GET", f"{GRAPH_BASE}/me/messages/{message_id}",
            params={"$select": _MESSAGE_SELECT},
            extra_headers={"Prefer": 'outlook.body-content-type="text"'},
        )

        received_at = datetime.fromisoformat(data["receivedDateTime"])
        sender = (data.get("from") or {}).get("emailAddress", {}).get("address", "")
        recipients = [
            r["emailAddress"]["address"] for r in data.get("toRecipients", []) if r.get("emailAddress", {}).get("address")
        ]

        attachments: list[Attachment] = []
        if data.get("hasAttachments"):
            attachments = self._fetch_attachments(message_id)

        return UnifiedEmail(
            provider=EmailProvider.OUTLOOK,
            account_id=self.account_id,
            message_id=data["id"],
            thread_id=data.get("conversationId") or data["id"],
            subject=data.get("subject") or "",
            sender=sender,
            recipients=recipients,
            received_at=received_at,
            body_text=(data.get("body") or {}).get("content", ""),
            attachments=attachments,
            source_url_or_reference=data.get("webLink"),
            # Gmail'in CATEGORY_PROMOTIONS/CATEGORY_SOCIAL etiketlerine karşılık
            # gelen deterministik bir otomatik-sınıflandırma sinyali Graph'ta
            # yok (`categories` yalnızca kullanıcının KENDİ elle atadığı
            # etiketler) — bu yüzden Outlook maili için labels her zaman boş,
            # is_calendar_worthy'nin etiket ön filtresi Outlook'ta hiç tetiklenmez
            # (bug değil, saf metin/LLM sınıflandırmasına düşüyor).
            labels=[],
        )

    def _fetch_attachments(self, message_id: str) -> list[Attachment]:
        data = self._request(
            "GET", f"{GRAPH_BASE}/me/messages/{message_id}/attachments",
            params={"$select": "id,name,contentType,size"},
        )
        return [
            Attachment(filename=a["name"], content_type=a.get("contentType"), size_bytes=a.get("size"), attachment_id=a["id"])
            for a in data.get("value", [])
            if a.get("@odata.type") == "#microsoft.graph.fileAttachment"
        ]

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Bir ekin GERÇEK bayt içeriğini çeker — GmailConnector.download_attachment
        ile AYNI sıfır-kalıcılık ilkesi (bkz. Attachment.attachment_id docstring'i)."""
        data = self._request("GET", f"{GRAPH_BASE}/me/messages/{message_id}/attachments/{attachment_id}")
        content_b64 = data.get("contentBytes")
        if not content_b64:
            raise RuntimeError(f"Ek dosya içeriği alınamadı: {attachment_id}")
        return base64.b64decode(content_b64)
