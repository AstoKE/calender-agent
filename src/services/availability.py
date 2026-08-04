"""Availability & Conflict Engine (bkz. docs/architecture-plan.md §6/§11).

Deterministik: çakışma tespiti ve alternatif slot arama saf tarih/saat
matematiğidir, LLM'e bırakılmaz (bkz. plan §11 "RAG için bağlam, kritik
kararlar için deterministik kod" ilkesi).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.connectors.google_calendar import GoogleCalendarConnector

WORKING_HOURS = (9, 19)  # 09:00-19:00, basit MVP varsayımı; ileride kullanıcı tercihine bağlanacak
SEARCH_WINDOW_DAYS = 5
SLOT_STEP_MINUTES = 30
MAX_SUGGESTIONS = 3


def find_conflicts(
    calendar: GoogleCalendarConnector, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """[start, end) aralığındaki mevcut meşgul aralıkları döner; boş liste = çakışma yok."""
    return calendar.get_freebusy(start, end)


def _overlaps(slot_start: datetime, slot_end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(slot_start < b_end and slot_end > b_start for b_start, b_end in busy)


def suggest_alternative_slots(
    calendar: GoogleCalendarConnector,
    duration_minutes: int,
    preferred_start: datetime,
) -> list[datetime]:
    """``preferred_start``'tan itibaren çalışma saatleri içinde uygun ilk birkaç
    boş slotu önerir. Tüm arama penceresi için TEK bir freebusy sorgusu yapar,
    sonra bellekte tarar (slot başına API çağrısı yapmaz)."""
    window_end = preferred_start + timedelta(days=SEARCH_WINDOW_DAYS)
    busy = calendar.get_freebusy(preferred_start, window_end)

    suggestions: list[datetime] = []
    slot = preferred_start
    step = timedelta(minutes=SLOT_STEP_MINUTES)

    while len(suggestions) < MAX_SUGGESTIONS and slot < window_end:
        day_start = slot.replace(hour=WORKING_HOURS[0], minute=0, second=0, microsecond=0)
        day_end = slot.replace(hour=WORKING_HOURS[1], minute=0, second=0, microsecond=0)

        if slot < day_start:
            slot = day_start
        if slot >= day_end:
            slot = day_start + timedelta(days=1)
            continue

        slot_end = slot + timedelta(minutes=duration_minutes)
        if slot_end <= day_end and not _overlaps(slot, slot_end, busy):
            suggestions.append(slot)
            slot += timedelta(hours=1)
        else:
            slot += step

    return suggestions
