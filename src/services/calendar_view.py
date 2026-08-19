"""Google Calendar API v3 ham JSON'unu Takvim ekranının render edebileceği
saf veri yapılarına çevirir (bkz. plan Faz 4). OAuth'suz, ağ çağrısı YOK —
tamamen fixture JSON ile test edilebilir (bkz. tests/test_calendar_view.py).

`GoogleCalendarConnector.list_events` ham `dict` döner (Google'ın kendi
şeması); bu modül onu ayrıştırırken üç bilinen tuzağa dikkat eder:

1. `dateTime` bazen 'Z' ile bitiyor — `datetime.fromisoformat` bunu yalnızca
   Python 3.11+'ta kabul ediyor, yine de normalize ediyoruz (garanti olsun).
2. Tüm-gün etkinliklerde `date` var, `dateTime` yok — saat grid'ine 00:00
   olarak KONULMAZ, ayrı bir "tüm gün" satırında gösterilir (bkz. DayBucket).
3. Google'ın tüm-gün `end.date`'i DIŞLAYICI: 19'unda başlayıp biten tek
   günlük bir etkinlikte `end.date == '2026-08-20'` gelir — olduğu gibi
   gösterilirse "19–20 Ağustos" gibi klasik bir off-by-one hatası olur, bu
   yüzden gösterim için bir gün geri alınır."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.localization.formatting import to_display_timezone


@dataclass(frozen=True)
class CalendarEntry:
    event_id: str
    title: str  # boş string olabilir — "(başlıksız)" gösterimi çağıranın (t()) işi
    start: datetime
    end: datetime
    all_day: bool
    location: str | None = None
    html_link: str | None = None


@dataclass
class DayBucket:
    day: date
    all_day_entries: list[CalendarEntry] = field(default_factory=list)
    timed_entries: list[CalendarEntry] = field(default_factory=list)


def _parse_google_datetime(raw: str, display_tz: str) -> datetime:
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    return to_display_timezone(datetime.fromisoformat(normalized), display_tz)


def parse_google_event(raw: dict, display_tz: str) -> CalendarEntry | None:
    """`status == 'cancelled'` olan etkinlikler `singleEvents=True` ile bile
    listede gelebilir — burada elenir. Ne `date` ne `dateTime` içeren
    (bozuk/beklenmeyen) bir kayıt için de None döner, çağıran atlar."""
    if raw.get("status") == "cancelled":
        return None

    start_raw = raw.get("start") or {}
    end_raw = raw.get("end") or {}

    if "date" in start_raw:
        all_day = True
        start = datetime.fromisoformat(start_raw["date"])
        end_date = datetime.fromisoformat(end_raw["date"]) if "date" in end_raw else start
        end = end_date - timedelta(days=1)  # Google'ın dışlayıcı end.date'i — bkz. modül docstring
        if end < start:
            end = start
    elif "dateTime" in start_raw:
        all_day = False
        start = _parse_google_datetime(start_raw["dateTime"], display_tz)
        end = _parse_google_datetime(end_raw["dateTime"], display_tz) if "dateTime" in end_raw else start
    else:
        return None

    return CalendarEntry(
        event_id=raw.get("id", ""),
        title=raw.get("summary") or "",
        start=start,
        end=end,
        all_day=all_day,
        location=raw.get("location"),
        html_link=raw.get("htmlLink"),
    )


def group_by_day(entries: list[CalendarEntry], first_day: date, days: int) -> list[DayBucket]:
    """`entries` görünür [first_day, first_day+days) penceresinin dışına
    taşan (çok günlük tüm-gün etkinlikler) parçaları görünür pencereye
    kırpar — pencerenin öncesine/sonrasına bucket oluşturmaz."""
    last_day = first_day + timedelta(days=days - 1)
    buckets: dict[date, DayBucket] = {
        first_day + timedelta(days=i): DayBucket(first_day + timedelta(days=i)) for i in range(days)
    }
    for entry in entries:
        d = max(entry.start.date(), first_day)
        end_d = min(entry.end.date(), last_day)
        while d <= end_d:
            bucket = buckets[d]
            (bucket.all_day_entries if entry.all_day else bucket.timed_entries).append(entry)
            d += timedelta(days=1)

    ordered = [buckets[first_day + timedelta(days=i)] for i in range(days)]
    for bucket in ordered:
        bucket.timed_entries.sort(key=lambda e: e.start)
    return ordered


def day_bounds(anchor: date, tz_name: str) -> tuple[datetime, datetime]:
    """`anchor` gününün 00:00'ından bir sonraki günün 00:00'ına kadar — Ana
    Sayfa'nın "bugün" şeridi için (bkz. plan Faz 6)."""
    tz = ZoneInfo(tz_name)
    start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def week_bounds(anchor: date, tz_name: str) -> tuple[datetime, datetime]:
    """`anchor`'ı içeren haftanın Pazartesi 00:00'ından bir sonraki Pazartesi
    00:00'ına kadar (bkz. src/localization/formatting.py _WEEKDAY_NAMES:
    0=Pazartesi) — GoogleCalendarConnector.list_events'e verilecek aralık."""
    monday = anchor - timedelta(days=anchor.weekday())
    tz = ZoneInfo(tz_name)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
    return start, start + timedelta(days=7)
