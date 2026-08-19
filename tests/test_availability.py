from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.services.availability import (
    MAX_SUGGESTIONS,
    WORKING_HOURS,
    find_conflicts,
    suggest_alternative_slots,
)

TZ = ZoneInfo("Europe/Istanbul")


class FakeCalendar:
    """find_conflicts/suggest_alternative_slots yalnızca get_freebusy'yi
    çağırıyor (duck typing) — GoogleCalendarConnector'dan miras almaya gerek yok."""

    def __init__(self, busy: list[tuple[datetime, datetime]]):
        self._busy = busy

    def get_freebusy(self, start, end):
        return self._busy


def dt(hour, minute=0, day=18):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


def test_find_conflicts_none():
    calendar = FakeCalendar(busy=[])
    assert find_conflicts(calendar, dt(14), dt(15)) == []


def test_find_conflicts_returns_busy_periods():
    busy = [(dt(14), dt(15))]
    calendar = FakeCalendar(busy=busy)
    assert find_conflicts(calendar, dt(14, 30), dt(15, 30)) == busy


def test_suggest_alternative_slots_skips_busy_period():
    busy = [(dt(14), dt(15))]
    calendar = FakeCalendar(busy=busy)
    suggestions = suggest_alternative_slots(calendar, duration_minutes=60, preferred_start=dt(14))
    assert suggestions
    for slot in suggestions:
        slot_end = slot + timedelta(minutes=60)
        assert not (slot < dt(15) and slot_end > dt(14))


def test_suggest_alternative_slots_respects_working_hours():
    calendar = FakeCalendar(busy=[])
    suggestions = suggest_alternative_slots(calendar, duration_minutes=60, preferred_start=dt(18, 30))
    for slot in suggestions:
        assert WORKING_HOURS[0] <= slot.hour < WORKING_HOURS[1]


def test_suggest_alternative_slots_respects_max_suggestions_cap():
    calendar = FakeCalendar(busy=[])
    suggestions = suggest_alternative_slots(calendar, duration_minutes=30, preferred_start=dt(9))
    assert len(suggestions) <= MAX_SUGGESTIONS


def test_suggest_alternative_slots_empty_when_fully_booked():
    # Arama penceresinin tamamı (5 gün, mesai saatleri) meşgul.
    busy = [(dt(0, day=d), dt(23, 59, day=d)) for d in range(18, 24)]
    calendar = FakeCalendar(busy=busy)
    suggestions = suggest_alternative_slots(calendar, duration_minutes=30, preferred_start=dt(9))
    assert suggestions == []
