"""src/services/calendar_view.py için deterministik testler — sıfır ağ
erişimi, sıfır OAuth: Google Calendar API v3'ün döndürdüğü ham JSON şekli
elle fixture'landı (bkz. plan Faz 4, ayrıştırma tuzakları)."""

from __future__ import annotations

from datetime import date, datetime

from src.services.calendar_view import (
    CalendarEntry,
    SLOTS_PER_DAY,
    adjacent_month_anchor,
    day_bounds,
    default_scroll_row,
    group_by_day,
    layout_timed_entries,
    month_grid,
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


# --- layout_timed_entries (saat-ızgarası geometrisi) ---


def test_layout_single_entry_row_slots():
    day = date(2026, 8, 19)
    e = CalendarEntry("a", "A", datetime(2026, 8, 19, 9, 0), datetime(2026, 8, 19, 10, 30), all_day=False)
    [pos] = layout_timed_entries([e], day)
    # 09:00 -> slot 36 (9*4), 10:30 -> slot 42 (10*4+2); grid-row 1-indexed
    assert pos.row_start == 37
    assert pos.row_end == 43
    assert pos.col_index == 0
    assert pos.col_count == 1


def test_layout_zero_length_entry_gets_minimum_one_slot():
    day = date(2026, 8, 19)
    e = CalendarEntry("a", "A", datetime(2026, 8, 19, 9, 0), datetime(2026, 8, 19, 9, 0), all_day=False)
    [pos] = layout_timed_entries([e], day)
    assert pos.row_end == pos.row_start + 1


def test_layout_non_overlapping_entries_share_single_column():
    day = date(2026, 8, 19)
    e1 = CalendarEntry("a", "A", datetime(2026, 8, 19, 9, 0), datetime(2026, 8, 19, 10, 0), all_day=False)
    e2 = CalendarEntry("b", "B", datetime(2026, 8, 19, 10, 0), datetime(2026, 8, 19, 11, 0), all_day=False)
    positions = layout_timed_entries([e1, e2], day)
    assert all(p.col_count == 1 and p.col_index == 0 for p in positions)


def test_layout_overlapping_entries_get_separate_columns():
    day = date(2026, 8, 19)
    e1 = CalendarEntry("a", "A", datetime(2026, 8, 19, 9, 0), datetime(2026, 8, 19, 10, 0), all_day=False)
    e2 = CalendarEntry("b", "B", datetime(2026, 8, 19, 9, 30), datetime(2026, 8, 19, 10, 30), all_day=False)
    positions = layout_timed_entries([e1, e2], day)
    by_id = {p.entry.event_id: p for p in positions}
    assert by_id["a"].col_index != by_id["b"].col_index
    assert by_id["a"].col_count == 2
    assert by_id["b"].col_count == 2


def test_layout_three_way_overlap_uses_three_columns():
    day = date(2026, 8, 19)
    entries = [
        CalendarEntry("a", "A", datetime(2026, 8, 19, 9, 0), datetime(2026, 8, 19, 11, 0), all_day=False),
        CalendarEntry("b", "B", datetime(2026, 8, 19, 9, 15), datetime(2026, 8, 19, 10, 0), all_day=False),
        CalendarEntry("c", "C", datetime(2026, 8, 19, 9, 30), datetime(2026, 8, 19, 10, 0), all_day=False),
    ]
    positions = layout_timed_entries(entries, day)
    col_indices = {p.entry.event_id: p.col_index for p in positions}
    assert len(set(col_indices.values())) == 3
    assert all(p.col_count == 3 for p in positions)


def test_layout_clips_multiday_timed_entry_to_visible_day_bounds():
    day = date(2026, 8, 19)
    spanning = CalendarEntry(
        "a", "A", datetime(2026, 8, 18, 22, 0), datetime(2026, 8, 20, 6, 0), all_day=False
    )
    [pos] = layout_timed_entries([spanning], day)
    assert pos.row_start == 1
    assert pos.row_end == SLOTS_PER_DAY + 1


# --- month_grid / adjacent_month_anchor (mini takvim, bkz. takvim.html sidebar) ---


def test_month_grid_always_six_weeks():
    weeks = month_grid(date(2026, 8, 19))
    assert len(weeks) == 6
    assert all(len(week) == 7 for week in weeks)


def test_month_grid_starts_on_monday():
    weeks = month_grid(date(2026, 8, 19))
    assert weeks[0][0].day.weekday() == 0


def test_month_grid_marks_days_outside_current_month():
    weeks = month_grid(date(2026, 8, 19))  # Ağustos 2026, 1'i Cumartesi
    first_week = weeks[0]
    assert first_week[0].day == date(2026, 7, 27)
    assert first_week[0].in_current_month is False
    august_first = next(d for week in weeks for d in week if d.day == date(2026, 8, 1))
    assert august_first.in_current_month is True


def test_month_grid_contains_every_day_of_the_month():
    weeks = month_grid(date(2026, 8, 19))
    days_in_month = [d.day for week in weeks for d in week if d.in_current_month]
    assert len(days_in_month) == 31
    assert days_in_month[0] == date(2026, 8, 1)
    assert days_in_month[-1] == date(2026, 8, 31)


def test_adjacent_month_anchor_next():
    assert adjacent_month_anchor(date(2026, 8, 19), 1) == date(2026, 9, 1)


def test_adjacent_month_anchor_prev():
    assert adjacent_month_anchor(date(2026, 8, 19), -1) == date(2026, 7, 1)


def test_adjacent_month_anchor_across_year_boundary():
    assert adjacent_month_anchor(date(2026, 1, 15), -1) == date(2025, 12, 1)
    assert adjacent_month_anchor(date(2026, 12, 15), 1) == date(2027, 1, 1)


# --- default_scroll_row (bkz. canlı testte bulunan sorun: ızgara 00:00'dan
# başlayıp sabah 7'den sonraki etkinlikleri gizliyordu) ---


def test_default_scroll_row_no_events_uses_fallback_hour():
    first_day = date(2026, 8, 17)
    buckets = group_by_day([], first_day, 7)
    # fallback_hour=7 -> row 29, lead_in_slots=2 -> 27
    assert default_scroll_row(buckets) == 27


def test_default_scroll_row_early_event_scrolls_before_it():
    first_day = date(2026, 8, 17)
    e = CalendarEntry("a", "A", datetime(2026, 8, 18, 6, 0), datetime(2026, 8, 18, 6, 30), all_day=False)
    buckets = group_by_day([e], first_day, 7)
    # 06:00 -> slot 24 -> row_start 25; lead_in_slots=2 -> 23
    assert default_scroll_row(buckets) == 23


def test_default_scroll_row_late_events_still_uses_fallback_hour():
    """Tüm etkinlikler fallback saatinden GEÇse bile fallback'ten daha ileri
    kaydırılmaz — boş sabahı gereksiz yere göstermemek için."""
    first_day = date(2026, 8, 17)
    e = CalendarEntry("a", "A", datetime(2026, 8, 18, 14, 0), datetime(2026, 8, 18, 15, 0), all_day=False)
    buckets = group_by_day([e], first_day, 7)
    assert default_scroll_row(buckets) == 27  # yine fallback_hour=7 temelli


def test_default_scroll_row_never_below_one():
    first_day = date(2026, 8, 17)
    e = CalendarEntry("a", "A", datetime(2026, 8, 18, 0, 0), datetime(2026, 8, 18, 0, 15), all_day=False)
    buckets = group_by_day([e], first_day, 7)
    assert default_scroll_row(buckets) == 1


def test_default_scroll_row_custom_fallback_and_lead_in():
    first_day = date(2026, 8, 17)
    buckets = group_by_day([], first_day, 7)
    assert default_scroll_row(buckets, fallback_hour=9, lead_in_slots=0) == 9 * 4 + 1
