"""Microsoft Graph tabanlı MS Calendar connector (bkz. docs/architecture-plan.md §13).

`GoogleCalendarConnector`'ın Microsoft Graph karşılığı — AYNI provider-nötr
`CalendarConnector` arayüzünü uygular (bkz. normalizasyon sızıntısı
düzeltmesi, CLAUDE.md Outlook bölümü); Graph'a özgü JSON şekli (`subject`,
`location.displayName`, ...) yalnızca BU dosyanın içinde bilinir.

create_event/update_event yalnızca Calendar Action Executor tarafından,
kullanıcı onayı sonrası çağrılmalıdır — bu connector kendi başına hiçbir
onay/doğrulama mantığı içermez (bkz. §11 Deterministic Rule Engine) —
GoogleCalendarConnector ile aynı ilke.
"""

from __future__ import annotations

from datetime import datetime

import requests

from src.connectors.base import CalendarConnector
from src.connectors.microsoft_auth import MS_ACCOUNT_SCOPES, get_ms_token
from src.services.timeutil import DEFAULT_TIMEZONE

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class MSCalendarConnector(CalendarConnector):
    def __init__(self, account_id: str, access_token: str | None = None):
        """``access_token`` verilirse OAuth akışı (get_ms_token) hiç
        çalıştırılmaz — GoogleCalendarConnector'daki AYNI desen: web yolu
        interaktif fallback'e asla düşmemesi gereken
        load_ms_token_noninteractive ile önceden token üretip buraya geçirir."""
        self.account_id = account_id
        self._token = access_token if access_token is not None else get_ms_token(MS_ACCOUNT_SCOPES, account_id)

    def _request(
        self, method: str, path: str, json_body: dict | None = None, params: dict | None = None
    ) -> dict:
        resp = requests.request(
            method,
            f"{GRAPH_BASE}{path}",
            json=json_body,
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Microsoft Graph API hatası ({resp.status_code}): {resp.text}")
        if not resp.content:
            return {}
        return resp.json()

    def list_events(self, time_min: datetime, time_max: datetime, calendar_id: str = "primary") -> list[dict]:
        # Graph'ta Google'daki gibi seçilebilir bir "primary" takvim kimliği
        # yok — kullanıcının varsayılan takvimi /me/calendar ile ifade
        # edilir, calendar_id burada YOK SAYILIR (bu proje zaten her yerde
        # sabit "primary" geçiyor, birden fazla takvim seçme özelliği yok).
        #
        # NOT: sorgu parametreleri BİLEREK `params=` ile veriliyor, f-string
        # ile URL'ye elle eklenmiyor — canlı testte gerçek bir hata bulundu:
        # ISO tarihindeki "+03:00" ofsetindeki "+" işareti, kodlanmamış bir
        # query string'de boşluğa dönüşüyor ("2026-08-26T17:24:23 03:00"),
        # Graph bunu geçersiz parametre olarak reddediyor. `requests`'in
        # `params=` desteği bunu doğru kodluyor.
        items: list[dict] = []
        params: dict | None = {
            "startDateTime": time_min.isoformat(),
            "endDateTime": time_max.isoformat(),
            "$orderby": "start/dateTime",
            "$top": 999,
        }
        url = f"{GRAPH_BASE}/me/calendarView"
        while url:
            resp = requests.get(url, params=params, headers={"Authorization": f"Bearer {self._token}"}, timeout=30)
            if not resp.ok:
                raise RuntimeError(f"Microsoft Graph API hatası ({resp.status_code}): {resp.text}")
            data = resp.json()
            items.extend(data.get("value", []))
            # @odata.nextLink Graph tarafından zaten TAM ve doğru kodlanmış
            # döner — ikinci turdan itibaren params tekrar eklenmemeli.
            url = data.get("@odata.nextLink")
            params = None
        return items

    def get_freebusy(
        self, time_min: datetime, time_max: datetime, calendar_id: str = "primary"
    ) -> list[tuple[datetime, datetime]]:
        me = self._request("GET", "/me")
        email = me.get("mail") or me["userPrincipalName"]
        body = {
            "schedules": [email],
            "startTime": {"dateTime": time_min.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            "endTime": {"dateTime": time_max.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            "availabilityViewInterval": 30,
        }
        resp = self._request("POST", "/me/calendar/getSchedule", body)
        schedules = resp.get("value", [])
        if not schedules:
            return []
        busy: list[tuple[datetime, datetime]] = []
        for item in schedules[0].get("scheduleItems", []):
            if item.get("status") in ("busy", "tentative", "oof"):
                start = datetime.fromisoformat(item["start"]["dateTime"])
                end = datetime.fromisoformat(item["end"]["dateTime"])
                busy.append((start, end))
        return busy

    def create_event(
        self,
        *,
        title: str | None,
        start: datetime,
        end: datetime,
        location: str | None = None,
        calendar_id: str = "primary",
    ) -> str:
        body: dict = {
            "subject": title,
            "start": {"dateTime": start.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            "end": {"dateTime": end.isoformat(), "timeZone": DEFAULT_TIMEZONE},
        }
        if location is not None:
            # Graph, Google'ın aksine konumu düz bir metin değil bir nesne
            # olarak bekliyor (canlı testte doğrulandı — bkz. CLAUDE.md).
            body["location"] = {"displayName": location}
        created = self._request("POST", "/me/events", body)
        return created["id"]

    def update_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        location: str | None = None,
        calendar_id: str = "primary",
    ) -> None:
        body: dict = {}
        if title is not None:
            body["subject"] = title
        if location is not None:
            body["location"] = {"displayName": location}
        if start is not None:
            body["start"] = {"dateTime": start.isoformat(), "timeZone": DEFAULT_TIMEZONE}
        if end is not None:
            body["end"] = {"dateTime": end.isoformat(), "timeZone": DEFAULT_TIMEZONE}
        self._request("PATCH", f"/me/events/{event_id}", body)

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None:
        self._request("DELETE", f"/me/events/{event_id}")
