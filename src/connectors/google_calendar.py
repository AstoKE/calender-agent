"""Google Calendar connector (bkz. docs/architecture-plan.md §13).

create_event/update_event yalnızca Calendar Action Executor tarafından,
kullanıcı onayı sonrası çağrılmalıdır — bu connector kendi başına hiçbir
onay/doğrulama mantığı içermez (bkz. §11 Deterministic Rule Engine).
"""

from __future__ import annotations

from datetime import datetime

from googleapiclient.discovery import build

from src.connectors.base import CalendarConnector
from src.connectors.google_auth import GOOGLE_ACCOUNT_SCOPES, get_google_credentials


class GoogleCalendarConnector(CalendarConnector):
    def __init__(self, account_id: str):
        self.account_id = account_id
        creds = get_google_credentials(GOOGLE_ACCOUNT_SCOPES, account_id)
        self._service = build("calendar", "v3", credentials=creds)

    def list_events(self, time_min: datetime, time_max: datetime, calendar_id: str = "primary") -> list[dict]:
        resp = (
            self._service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return resp.get("items", [])

    def get_freebusy(
        self, time_min: datetime, time_max: datetime, calendar_id: str = "primary"
    ) -> list[tuple[datetime, datetime]]:
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": calendar_id}],
        }
        resp = self._service.freebusy().query(body=body).execute()
        busy = resp["calendars"][calendar_id]["busy"]
        return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in busy]

    def create_event(self, event: dict, calendar_id: str = "primary") -> str:
        created = self._service.events().insert(calendarId=calendar_id, body=event).execute()
        return created["id"]

    def update_event(self, event_id: str, changes: dict, calendar_id: str = "primary") -> None:
        self._service.events().patch(calendarId=calendar_id, eventId=event_id, body=changes).execute()

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None:
        self._service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
