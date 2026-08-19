from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.services.vertical_prototype import _find_matching_events

TZ = ZoneInfo("Europe/Istanbul")


class FakeCalendar:
    """_find_matching_events yalnızca list_events'i çağırıyor (duck typing) —
    gerçek Google API'ye dokunmadan test edilebiliyor. Çağrıldığı aralığı da
    kaydeder, ki date_hint'in doğru pencereyi hesapladığı doğrulanabilsin."""

    def __init__(self, events: list[dict]):
        self._events = events
        self.last_call: tuple[datetime, datetime] | None = None

    def list_events(self, time_min, time_max, calendar_id="primary"):
        self.last_call = (time_min, time_max)
        return self._events


def _event(summary, start_iso):
    return {"id": summary, "summary": summary, "start": {"dateTime": start_iso}}


def test_no_hints_searches_next_14_days_and_returns_all():
    events = [_event("Toplantı A", "2026-08-20T14:00:00+03:00")]
    calendar = FakeCalendar(events)
    result = _find_matching_events(calendar, title_hint=None, date_hint=None)
    assert result == events
    window_start, window_end = calendar.last_call
    assert (window_end - window_start) == timedelta(days=14)


def test_date_hint_searches_single_day_window():
    calendar = FakeCalendar([])
    _find_matching_events(calendar, title_hint=None, date_hint="2026-08-20")
    window_start, window_end = calendar.last_call
    assert window_start == datetime(2026, 8, 20, 0, 0, tzinfo=TZ)
    assert window_end == datetime(2026, 8, 21, 0, 0, tzinfo=TZ)


def test_title_hint_filters_by_case_insensitive_substring():
    events = [
        _event("Proje Toplantısı", "2026-08-20T14:00:00+03:00"),
        _event("Diş Randevusu", "2026-08-20T16:00:00+03:00"),
    ]
    calendar = FakeCalendar(events)
    result = _find_matching_events(calendar, title_hint="proje", date_hint=None)
    assert len(result) == 1
    assert result[0]["summary"] == "Proje Toplantısı"


def test_title_hint_with_no_match_falls_back_to_full_window():
    events = [_event("Diş Randevusu", "2026-08-20T16:00:00+03:00")]
    calendar = FakeCalendar(events)
    result = _find_matching_events(calendar, title_hint="olmayan başlık", date_hint=None)
    assert result == events


def test_no_events_returns_empty_list():
    calendar = FakeCalendar([])
    assert _find_matching_events(calendar, title_hint="herhangi", date_hint=None) == []
