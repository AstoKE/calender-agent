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
    # Saat-ızgarası (Takvim ekranı, bkz. layout_timed_entries) için hesaplanmış
    # yerleşim — group_by_day tarafından timed_entries doldurulduktan SONRA
    # ayrıca hesaplanır. Ayrı bir alan: timed_entries'in kendisi hâlâ "ham"
    # kalıyor (mevcut testler ve olası başka tüketiciler için), grid'e özgü
    # geometri (row_start/row_end/col_index/col_count) buraya taşınıyor.
    positioned_entries: list["PositionedEntry"] = field(default_factory=list)


# Saat-ızgarasında bir "satır" 15 dakikaya karşılık gelir — CSS
# grid-template-rows: repeat(96, ...) ile hizalanır (bkz. takvim.html).
SLOT_MINUTES = 15
SLOTS_PER_DAY = (24 * 60) // SLOT_MINUTES  # 96


@dataclass(frozen=True)
class PositionedEntry:
    """Bir CalendarEntry'nin saat-ızgarasındaki geometrisi. row_start/row_end
    1-indexed CSS grid-row değerleridir (CSS grid-row'un kendisi 1-indexed
    ve `end` dışlayıcıdır — bu yüzden burada da aynı kural: row_end, entry'nin
    bittiği slot+1). col_index/col_count aynı saatte çakışan etkinliklerin
    yan yana (dar sütunlar halinde) gösterilmesi için — bkz. layout_timed_entries."""

    entry: CalendarEntry
    row_start: int
    row_end: int
    col_index: int
    col_count: int


def _time_to_slot(dt: datetime, day: date) -> int:
    """dt'nin `day` içindeki 15-dakikalık slot indeksini (0-96) döner. Çok
    günlük (ama tüm-gün OLMAYAN — nadir ama Google Calendar'da mümkün)
    etkinlikler `day`'in dışına taşan uçlara sahip olabilir; bu durumda
    günün başına/sonuna kırpılır (0 veya SLOTS_PER_DAY)."""
    if dt.date() < day:
        return 0
    if dt.date() > day:
        return SLOTS_PER_DAY
    return (dt.hour * 60 + dt.minute) // SLOT_MINUTES


def layout_timed_entries(entries: list[CalendarEntry], day: date) -> list[PositionedEntry]:
    """Saatli etkinlikleri saat-ızgarasında konumlandırır: dikey eksende
    (row_start/row_end) başlangıç/bitiş saatine göre, yatay eksende
    (col_index/col_count) aynı ana denk gelen etkinlikler açgözlü bir
    interval-graph coloring ile yan yana sütunlara dağıtılır (klasik takvim
    uygulamalarının çakışan etkinlikleri gösterme yöntemi — optimal sütun
    sayısını garanti etmez ama basit ve her zaman doğru/okunabilir sonuç verir)."""
    raw = []
    for e in entries:
        start_slot = _time_to_slot(e.start, day)
        end_slot = max(_time_to_slot(e.end, day), start_slot + 1)  # en az 1 slot yükseklik
        raw.append((start_slot, end_slot, e))
    raw.sort(key=lambda item: (item[0], item[1]))

    columns_occupied_until: list[int] = []
    assigned: list[tuple[int, int, int, CalendarEntry]] = []
    for start_slot, end_slot, e in raw:
        for col_idx, occupied_until in enumerate(columns_occupied_until):
            if occupied_until <= start_slot:
                columns_occupied_until[col_idx] = end_slot
                assigned.append((start_slot, end_slot, col_idx, e))
                break
        else:
            columns_occupied_until.append(end_slot)
            assigned.append((start_slot, end_slot, len(columns_occupied_until) - 1, e))

    result = []
    for start_slot, end_slot, col_idx, e in assigned:
        overlapping_cols = {
            c for s2, e2, c, _ in assigned if s2 < end_slot and e2 > start_slot
        }
        col_count = max(overlapping_cols) + 1
        result.append(
            PositionedEntry(
                entry=e, row_start=start_slot + 1, row_end=end_slot + 1,
                col_index=col_idx, col_count=col_count,
            )
        )
    return result


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
        bucket.positioned_entries = layout_timed_entries(bucket.timed_entries, bucket.day)
    return ordered


def default_scroll_row(buckets: list[DayBucket], fallback_hour: int = 7, lead_in_slots: int = 2) -> int:
    """Takvim ekranının saat-ızgarasının İLK açılışta kaydırılacağı satır —
    canlı testte bulundu: ızgara 00:00'dan başlayıp `.timegrid-scroll`
    (bkz. components.css, max-height: 34rem) yalnızca birkaç saati
    gösterdiğinden, o hafta tüm etkinlikler sabah 7'den sonraysa (yaygın
    durum) kullanıcı hiçbir şey görmeden boş bir gece yarısı görünümüyle
    karşılaşıyor, ilk etkinliği görmek için elle aşağı kaydırması gerekiyordu.

    Görüntülenen haftadaki EN ERKEN etkinliğin satırından (varsa) biraz
    önce başlar; erken bir etkinlik yoksa (ya da hepsi `fallback_hour`'dan
    GEÇse — o zaman da hâlâ `fallback_hour`, boş sabahı göstermeye gerek
    yok) `fallback_hour`'dan başlar. Sonuç asla 1'in altına inmez (grid
    1-indexed)."""
    fallback_row = fallback_hour * (60 // SLOT_MINUTES) + 1
    earliest_row = min(
        (pos.row_start for bucket in buckets for pos in bucket.positioned_entries),
        default=fallback_row,
    )
    target_row = min(earliest_row, fallback_row)
    return max(1, target_row - lead_in_slots)


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


@dataclass(frozen=True)
class MiniCalendarDay:
    day: date
    in_current_month: bool


def month_grid(anchor: date) -> list[list[MiniCalendarDay]]:
    """`anchor`'ı içeren ayın Pazartesi-başlangıçlı, her zaman 6 haftalık
    (42 günlük) mini takvim ızgarası — önceki/sonraki aydan taşan günler de
    dahil (bkz. Takvim ekranı sol paneli). Sabit 6 hafta: bazı aylar 4 hafta
    hücresine sığar, bazıları 6 ister — sabit sayı kullanmak ay değiştikçe
    ızgaranın yüksekliğinin zıplamasını önler."""
    first_of_month = anchor.replace(day=1)
    first_grid_day = first_of_month - timedelta(days=first_of_month.weekday())
    weeks = []
    d = first_grid_day
    for _ in range(6):
        week = [MiniCalendarDay(day=d + timedelta(days=i), in_current_month=(d + timedelta(days=i)).month == anchor.month) for i in range(7)]
        weeks.append(week)
        d += timedelta(days=7)
    return weeks


def adjacent_month_anchor(anchor: date, direction: int) -> date:
    """Mini takvimin ay ileri/geri okları için: `direction` +1 veya -1,
    döner o yönde komşu ayın 1'i. 32 gün eklemek/1 gün çıkarmak, ayın kaç
    gün sürdüğünü bilmeden her zaman komşu aya düşmeyi garanti eden bilinen
    bir teknik (bkz. month_grid'in first_grid_day hesabıyla aynı fikir)."""
    first_of_month = anchor.replace(day=1)
    if direction < 0:
        return (first_of_month - timedelta(days=1)).replace(day=1)
    return (first_of_month + timedelta(days=32)).replace(day=1)
