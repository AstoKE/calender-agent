"""EVENT-01 (bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md): connector yazma
sözleşmesinin `reminders` alanını GERÇEKTEN sağlayıcının beklediği JSON
şekline çevirdiğini doğrular — Google (çoklu override destekli) ve
Microsoft Graph (tek `reminderMinutesBeforeStart` alanı, kapasite farkı
logla görünür kılınmalı) ayrı ayrı test edilir. Gerçek API'ye hiç
dokunulmaz: Google için `_service`'in kendisi sahte bir chain ile,
Microsoft için `requests.request` sahtelenerek."""

from __future__ import annotations

import logging

from src.connectors import ms_calendar as ms_calendar_module
from src.connectors.google_calendar import GoogleCalendarConnector
from src.connectors.ms_calendar import MSCalendarConnector
from datetime import datetime, timezone

START = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc)


# --- Google Calendar ---------------------------------------------------


class _FakeExecutable:
    def __init__(self, result, capture, kwargs):
        self._result = result
        capture.append(kwargs)

    def execute(self):
        return self._result


class _FakeEvents:
    def __init__(self):
        self.insert_calls: list[dict] = []
        self.patch_calls: list[dict] = []

    def insert(self, *, calendarId, body):
        return _FakeExecutable({"id": "evt-1"}, self.insert_calls, {"calendarId": calendarId, "body": body})

    def patch(self, *, calendarId, eventId, body):
        return _FakeExecutable({}, self.patch_calls, {"calendarId": calendarId, "eventId": eventId, "body": body})


def _google_connector():
    connector = object.__new__(GoogleCalendarConnector)
    connector.account_id = "acc1"
    fake_events = _FakeEvents()
    connector._service = type("FakeService", (), {"events": lambda self: fake_events})()
    return connector, fake_events


def test_google_create_event_without_reminders_omits_the_field():
    connector, events = _google_connector()
    connector.create_event(title="Toplantı", start=START, end=END)
    assert "reminders" not in events.insert_calls[0]["body"]


def test_google_create_event_sends_multiple_reminder_overrides():
    connector, events = _google_connector()
    connector.create_event(
        title="Sınav", start=START, end=END,
        reminders=[{"minutes_before": 4320}, {"minutes_before": 30}],
    )
    body = events.insert_calls[0]["body"]
    assert body["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 4320}, {"method": "popup", "minutes": 30}],
    }


def test_google_update_event_with_empty_reminders_clears_them():
    connector, events = _google_connector()
    connector.update_event("evt-1", reminders=[])
    body = events.patch_calls[0]["body"]
    assert body["reminders"] == {"useDefault": False, "overrides": []}


def test_google_create_event_truncates_to_five_overrides(caplog):
    connector, events = _google_connector()
    six_reminders = [{"minutes_before": m} for m in (10, 20, 30, 40, 50, 60)]
    with caplog.at_level(logging.WARNING, logger="calendar_agent.google_calendar"):
        connector.create_event(title="Çok hatırlatıcılı", start=START, end=END, reminders=six_reminders)
    body = events.insert_calls[0]["body"]
    assert len(body["reminders"]["overrides"]) == 5
    assert "API sınırı" in caplog.text


# --- Microsoft Graph -----------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data if json_data is not None else {}
        self.content = b"x"
        self.text = str(json_data)

    def json(self):
        return self._json_data


def _ms_connector(monkeypatch, captured: list[dict]):
    def fake_request(method, url, *, params=None, json=None, headers=None, timeout=None):
        captured.append({"method": method, "url": url, "json": json})
        return _FakeResponse(200, {"id": "evt-1"})

    monkeypatch.setattr(ms_calendar_module.requests, "request", fake_request)
    return MSCalendarConnector(account_id="acc1", access_token="FAKE_TOKEN")


def test_ms_create_event_without_reminders_omits_reminder_fields(monkeypatch):
    captured: list[dict] = []
    connector = _ms_connector(monkeypatch, captured)
    connector.create_event(title="Toplantı", start=START, end=END)
    body = captured[0]["json"]
    assert "isReminderOn" not in body
    assert "reminderMinutesBeforeStart" not in body


def test_ms_create_event_with_single_reminder_sets_provider_field(monkeypatch):
    captured: list[dict] = []
    connector = _ms_connector(monkeypatch, captured)
    connector.create_event(title="Sınav", start=START, end=END, reminders=[{"minutes_before": 4320}])
    body = captured[0]["json"]
    assert body["isReminderOn"] is True
    assert body["reminderMinutesBeforeStart"] == 4320


def test_ms_create_event_with_empty_reminders_turns_reminder_off(monkeypatch):
    captured: list[dict] = []
    connector = _ms_connector(monkeypatch, captured)
    connector.create_event(title="Toplantı", start=START, end=END, reminders=[])
    body = captured[0]["json"]
    assert body["isReminderOn"] is False


def test_ms_create_event_with_multiple_reminders_keeps_nearest_and_logs_capability_gap(monkeypatch, caplog):
    # Graph, Google'ın aksine tek hatırlatıcı destekliyor — bu, EVENT-01'in
    # "sağlayıcı yetenek farkları görünür" kabul kriterinin karşılığı: fazla
    # hatırlatıcı sessizce kaybolmuyor, hangisinin tutulduğu loglanıyor.
    captured: list[dict] = []
    connector = _ms_connector(monkeypatch, captured)
    with caplog.at_level(logging.WARNING, logger="calendar_agent.ms_calendar"):
        connector.create_event(
            title="Sınav", start=START, end=END,
            reminders=[{"minutes_before": 4320}, {"minutes_before": 30}],
        )
    body = captured[0]["json"]
    assert body["reminderMinutesBeforeStart"] == 30  # etkinliğe en yakın olan
    assert "tek hatırlatıcı alanı sınırı" in caplog.text


def test_ms_update_event_passes_reminder_through(monkeypatch):
    captured: list[dict] = []
    connector = _ms_connector(monkeypatch, captured)
    connector.update_event("evt-1", reminders=[{"minutes_before": 60}])
    body = captured[0]["json"]
    assert body["isReminderOn"] is True
    assert body["reminderMinutesBeforeStart"] == 60
