from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.services.timeutil import (
    DEFAULT_TIMEZONE,
    ensure_timezone,
    format_date_tr,
    parse_clock_time,
    parse_duration_minutes,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("13:00", "13:00"),
        ("11.00 da", "11:00"),
        ("13", "13:00"),
        ("saat 9", "09:00"),
        ("9.30", "09:30"),
    ],
)
def test_parse_clock_time_valid(raw, expected):
    assert parse_clock_time(raw) == expected


@pytest.mark.parametrize("raw", ["25:00", "13:70", "", "yarın"])
def test_parse_clock_time_invalid(raw):
    assert parse_clock_time(raw) is None


@pytest.mark.parametrize(
    "raw, expected_minutes",
    [
        ("30", 30),
        ("1 saat", 60),
        ("1.5 saat", 90),
        ("1,5 saat", 90),
        ("90 dakika", 90),
        ("45", 45),
    ],
)
def test_parse_duration_minutes_valid(raw, expected_minutes):
    assert parse_duration_minutes(raw) == expected_minutes


@pytest.mark.parametrize("raw", ["", "  ", "bilmiyorum"])
def test_parse_duration_minutes_invalid(raw):
    assert parse_duration_minutes(raw) is None


def test_ensure_timezone_none_and_empty():
    assert ensure_timezone(None) is None
    assert ensure_timezone("") is None


def test_ensure_timezone_naive_datetime_gets_default_tz():
    naive = datetime(2026, 8, 18, 14, 0)
    result = ensure_timezone(naive)
    assert result.tzinfo == ZoneInfo(DEFAULT_TIMEZONE)
    assert result.hour == 14


def test_ensure_timezone_aware_datetime_untouched():
    aware = datetime(2026, 8, 18, 14, 0, tzinfo=ZoneInfo("UTC"))
    result = ensure_timezone(aware)
    assert result is aware


def test_ensure_timezone_iso_string():
    result = ensure_timezone("2026-08-18T14:00:00")
    assert result.tzinfo == ZoneInfo(DEFAULT_TIMEZONE)
    assert result.year == 2026 and result.month == 8 and result.day == 18


def test_format_date_tr_uses_turkish_month_names():
    assert format_date_tr(datetime(2026, 8, 18)) == "18 Ağustos"
    assert format_date_tr(datetime(2026, 1, 5)) == "05 Ocak"
