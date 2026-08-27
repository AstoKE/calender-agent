from datetime import datetime, timedelta, timezone

from src.services.calendar_cache import CACHE_TTL_SECONDS, get_cached_events, list_events_cached, store_events
from src.storage.db import get_connection

ACCOUNT_ID = "acc1"


def _insert_account(account_id=ACCOUNT_ID):
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (id, provider, account_type, email, connected_at, status) "
            "VALUES (?, 'google', 'personal', ?, ?, 'active')",
            (account_id, f"{account_id}@example.com", now),
        )


def _event(event_id="evt1", summary="Toplantı", start_iso="2026-08-20T14:00:00+00:00", end_iso="2026-08-20T15:00:00+00:00"):
    return {
        "id": event_id,
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }


class _CountingCalendar:
    def __init__(self, events):
        self._events = events
        self.call_count = 0

    def list_events(self, time_min, time_max, calendar_id="primary"):
        self.call_count += 1
        return self._events


def test_get_cached_events_returns_none_when_never_synced(temp_db):
    _insert_account()
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert get_cached_events(ACCOUNT_ID, "primary", time_min, time_max) is None


def test_store_then_get_within_range_returns_cached_raw_events(temp_db):
    _insert_account()
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    events = [_event()]
    store_events(ACCOUNT_ID, "primary", "google", time_min, time_max, events)

    # Daha DAR bir alt-aralık isteniyor — senkronize edilen aralığın içinde
    # kaldığı için yine cache'ten dönmeli.
    sub_min = datetime(2026, 8, 18, tzinfo=timezone.utc)
    sub_max = datetime(2026, 8, 19, tzinfo=timezone.utc)
    cached = get_cached_events(ACCOUNT_ID, "primary", sub_min, sub_max)
    assert cached == events


def test_get_cached_events_returns_none_when_range_not_contained(temp_db):
    _insert_account()
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store_events(ACCOUNT_ID, "primary", "google", time_min, time_max, [_event()])

    # İstenen aralık senkronize edilenin DIŞINA taşıyor (bir sonraki hafta).
    outside_min = datetime(2026, 8, 24, tzinfo=timezone.utc)
    outside_max = datetime(2026, 8, 31, tzinfo=timezone.utc)
    assert get_cached_events(ACCOUNT_ID, "primary", outside_min, outside_max) is None


def test_get_cached_events_returns_none_when_ttl_expired(temp_db, monkeypatch):
    _insert_account()
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store_events(ACCOUNT_ID, "primary", "google", time_min, time_max, [_event()])

    # synced_at'i TTL'in ötesine manuel olarak geri al.
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS + 10)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE calendar_sync_state SET synced_at = ? WHERE account_id = ? AND calendar_id = ?",
            (stale_time, ACCOUNT_ID, "primary"),
        )

    assert get_cached_events(ACCOUNT_ID, "primary", time_min, time_max) is None


def test_store_events_replaces_previous_cache_for_same_account_and_calendar(temp_db):
    _insert_account()
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store_events(ACCOUNT_ID, "primary", "google", time_min, time_max, [_event(event_id="old-evt")])
    store_events(ACCOUNT_ID, "primary", "google", time_min, time_max, [_event(event_id="new-evt")])

    cached = get_cached_events(ACCOUNT_ID, "primary", time_min, time_max)
    assert [e["id"] for e in cached] == ["new-evt"]


def test_list_events_cached_calls_live_only_once_for_repeated_same_range(temp_db):
    _insert_account()
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    calendar = _CountingCalendar([_event()])

    first = list_events_cached(calendar, ACCOUNT_ID, "google", time_min, time_max)
    second = list_events_cached(calendar, ACCOUNT_ID, "google", time_min, time_max)

    assert first == second == [_event()]
    assert calendar.call_count == 1


def test_list_events_cached_calls_live_again_for_different_range(temp_db):
    _insert_account()
    calendar = _CountingCalendar([_event()])

    list_events_cached(
        calendar, ACCOUNT_ID, "google", datetime(2026, 8, 17, tzinfo=timezone.utc), datetime(2026, 8, 24, tzinfo=timezone.utc)
    )
    list_events_cached(
        calendar, ACCOUNT_ID, "google", datetime(2026, 8, 24, tzinfo=timezone.utc), datetime(2026, 8, 31, tzinfo=timezone.utc)
    )

    assert calendar.call_count == 2


def test_different_accounts_do_not_share_cache(temp_db):
    _insert_account("acc1")
    _insert_account("acc2")
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store_events("acc1", "primary", "google", time_min, time_max, [_event()])

    assert get_cached_events("acc2", "primary", time_min, time_max) is None


def test_store_events_handles_outlook_shaped_events(temp_db):
    # Canlı testte bulunan gerçek çökme: Outlook/Graph etkinliklerinde
    # "location" düz metin değil bir NESNE (`{"displayName": ...}`) —
    # sqlite3 bunu bir sütuna bağlamaya çalışınca
    # "Error binding parameter: type 'dict' is not supported" ile
    # patlıyordu. Başlık da "summary" değil "subject" alanında.
    _insert_account("outlook_acc")
    time_min = datetime(2026, 8, 17, tzinfo=timezone.utc)
    time_max = datetime(2026, 8, 24, tzinfo=timezone.utc)
    outlook_event = {
        "id": "evt1",
        "subject": "Ekip Toplantısı",
        "isCancelled": False,
        "start": {"dateTime": "2026-08-20T14:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-08-20T15:00:00.0000000", "timeZone": "UTC"},
        "location": {"displayName": "Toplantı Odası 3"},
    }

    store_events("outlook_acc", "primary", "outlook", time_min, time_max, [outlook_event])  # çökmemeli

    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider, title, location FROM calendar_events_cache WHERE account_id = ?",
            ("outlook_acc",),
        ).fetchone()
    assert row["provider"] == "outlook"
    assert row["title"] == "Ekip Toplantısı"
    assert row["location"] == "Toplantı Odası 3"
