"""src/services/calendar_view.py için deterministik testler — sıfır ağ
erişimi, sıfır OAuth: Google Calendar API v3'ün döndürdüğü ham JSON şekli
elle fixture'landı (bkz. plan Faz 4, ayrıştırma tuzakları)."""

from __future__ import annotations

from datetime import date, datetime

from src.services.calendar_view import (
    CalendarEntry,
    day_bounds,
    group_by_day,
    parse_google_event,
    week_bounds,
)

TZ = "Europe/Istanbul"


def test_parse_timed_event():
    raw = {
        "id": "evt1",
        "summary": "Proje Toplantısı",
        "status": "confirmed",
        "start": {"dateTime": "2026-08-19T14:00:00+03:00"},
        "end": {"dateTime": "2026-08-19T15:00:00+03:00"},
        "location": "Ofis",
    }
    entry = parse_google_event(raw, TZ)
    assert entry.event_id == "evt1"
    assert entry.title == "Proje Toplantısı"
    assert entry.all_day is False
    assert entry.start.hour == 14
    assert entry.end.hour == 15
    assert entry.location == "Ofis"


def test_parse_event_with_z_suffix_datetime():
    raw = {
        "id": "evt2",
        "summary": "UTC olay",
        "start": {"dateTime": "2026-08-19T11:00:00Z"},
        "end": {"dateTime": "2026-08-19T12:00:00Z"},
    }
    entry = parse_google_event(raw, TZ)
    # Europe/Istanbul UTC+3 (yaz saati sonrası sabit) -> 11:00 UTC -> 14:00
    assert entry.start.hour == 14
    assert entry.start.tzinfo is not None


def test_parse_all_day_single_day_end_is_exclusive_and_corrected():
    raw = {
        "id": "evt3",
        "summary": "Tam gün",
        "start": {"date": "2026-08-19"},
        "end": {"date": "2026-08-20"},  # Google'ın dışlayıcı end.date'i
    }
    entry = parse_google_event(raw, TZ)
    assert entry.all_day is True
    assert entry.start.date() == date(2026, 8, 19)
    assert entry.end.date() == date(2026, 8, 19)  # 20'si DEĞİL — bir gün geri alınmış olmalı


def test_parse_all_day_multi_day_end_is_exclusive_and_corrected():
    raw = {
        "id": "evt4",
        "summary": "Üç günlük",
        "start": {"date": "2026-08-19"},
        "end": {"date": "2026-08-22"},  # 19, 20, 21'i kapsar, 22 dahil değil
    }
    entry = parse_google_event(raw, TZ)
    assert entry.start.date() == date(2026, 8, 19)
    assert entry.end.date() == date(2026, 8, 21)


def test_parse_cancelled_event_returns_none():
    raw = {
        "id": "evt5",
        "summary": "İptal edildi",
        "status": "cancelled",
        "start": {"dateTime": "2026-08-19T14:00:00+03:00"},
        "end": {"dateTime": "2026-08-19T15:00:00+03:00"},
    }
    assert parse_google_event(raw, TZ) is None


def test_parse_missing_summary_returns_empty_string_not_none_string():
    raw = {
        "id": "evt6",
        "start": {"dateTime": "2026-08-19T14:00:00+03:00"},
        "end": {"dateTime": "2026-08-19T15:00:00+03:00"},
    }
    entry = parse_google_event(raw, TZ)
    assert entry.title == ""


def test_parse_event_without_date_fields_returns_none():
    raw = {"id": "evt7", "summary": "bozuk kayıt", "status": "confirmed"}
    assert parse_google_event(raw, TZ) is None


def test_parse_missing_end_falls_back_to_start():
    raw = {
        "id": "evt8",
        "summary": "bitişsiz",
        "start": {"dateTime": "2026-08-19T14:00:00+03:00"},
    }
    entry = parse_google_event(raw, TZ)
    assert entry.end == entry.start


# --- group_by_day ---


def test_group_by_day_buckets_by_date():
    first_day = date(2026, 8, 17)
    e1 = CalendarEntry("a", "A", datetime(2026, 8, 17, 10), datetime(2026, 8, 17, 11), all_day=False)
    e2 = CalendarEntry("b", "B", datetime(2026, 8, 18, 9), datetime(2026, 8, 18, 9), all_day=False)
    buckets = group_by_day([e1, e2], first_day, 7)
    assert len(buckets) == 7
    assert buckets[0].day == date(2026, 8, 17)
    assert [e.event_id for e in buckets[0].timed_entries] == ["a"]
    assert [e.event_id for e in buckets[1].timed_entries] == ["b"]
    assert buckets[2].timed_entries == []


def test_group_by_day_separates_all_day_from_timed():
    first_day = date(2026, 8, 17)
    timed = CalendarEntry("a", "A", datetime(2026, 8, 17, 10), datetime(2026, 8, 17, 11), all_day=False)
    all_day = CalendarEntry("b", "B", datetime(2026, 8, 17), datetime(2026, 8, 17), all_day=True)
    buckets = group_by_day([timed, all_day], first_day, 7)
    assert [e.event_id for e in buckets[0].timed_entries] == ["a"]
    assert [e.event_id for e in buckets[0].all_day_entries] == ["b"]


def test_group_by_day_multi_day_all_day_spans_multiple_buckets():
    first_day = date(2026, 8, 17)
    spanning = CalendarEntry(
        "m", "Multi", datetime(2026, 8, 17), datetime(2026, 8, 19), all_day=True
    )
    buckets = group_by_day([spanning], first_day, 7)
    assert all("m" in [e.event_id for e in buckets[i].all_day_entries] for i in range(3))
    assert "m" not in [e.event_id for e in buckets[3].all_day_entries]


def test_group_by_day_clips_to_visible_window():
    first_day = date(2026, 8, 17)
    before_window = CalendarEntry("early", "E", datetime(2026, 8, 10), datetime(2026, 8, 10), all_day=False)
    buckets = group_by_day([before_window], first_day, 7)
    assert all(e.event_id != "early" for b in buckets for e in b.timed_entries)


def test_group_by_day_sorts_timed_entries_within_a_day():
    first_day = date(2026, 8, 17)
    late = CalendarEntry("late", "L", datetime(2026, 8, 17, 16), datetime(2026, 8, 17, 17), all_day=False)
    early = CalendarEntry("early", "E", datetime(2026, 8, 17, 9), datetime(2026, 8, 17, 10), all_day=False)
    buckets = group_by_day([late, early], first_day, 7)
    assert [e.event_id for e in buckets[0].timed_entries] == ["early", "late"]


# --- week_bounds ---


def test_week_bounds_starts_on_monday():
    # 2026-08-19 bir Çarşamba
    start, end = week_bounds(date(2026, 8, 19), TZ)
    assert start.weekday() == 0  # Pazartesi
    assert start.date() == date(2026, 8, 17)
    assert (end - start).days == 7


def test_week_bounds_anchor_already_monday():
    start, end = week_bounds(date(2026, 8, 17), TZ)
    assert start.date() == date(2026, 8, 17)
    assert (end - start).days == 7


def test_week_bounds_is_timezone_aware():
    start, end = week_bounds(date(2026, 8, 19), TZ)
    assert start.tzinfo is not None
    assert end.tzinfo is not None


# --- day_bounds ---


def test_day_bounds_spans_exactly_one_day():
    start, end = day_bounds(date(2026, 8, 19), TZ)
    assert start.date() == date(2026, 8, 19)
    assert (end - start).days == 1
    assert start.tzinfo is not None
