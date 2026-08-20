"""Dile göre tarih/saat biçimlendirme.

`locale.setlocale`/`%B` KULLANILMAZ: src/services/timeutil.py:17-22 bunun
canlı testte sistem locale'ine bağlı olarak İngilizce ay adı ürettiğini
belgeliyor; ayrıca `setlocale` süreç-global ve thread-safe değil (FastAPI
sync route'ları bir threadpool'da çalışır — bu, eşzamanlı istekleri
bozabilir). Bunun yerine elle yazılmış ay/gün adı tabloları kullanılır.

Her iki dilde de 24 saatlik format: burada "en" "Türk kullanıcı için
İngilizce arayüz" anlamına gelir, en-US değil — 12 saatlik format takvim
grid'inde ve düzenleme formunda tutarsızlığa yol açardı. Bu sonradan
`localization_preferences.date_format_pref` ile tersine çevrilebilir."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.localization import normalize_language

_MONTH_NAMES: dict[str, list[str]] = {
    "tr": ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
           "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"],
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
}

_WEEKDAY_NAMES: dict[str, list[str]] = {
    # datetime.weekday(): 0=Pazartesi
    "tr": ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}

_TODAY_KEY = {"tr": "Bugün", "en": "Today"}
_TOMORROW_KEY = {"tr": "Yarın", "en": "Tomorrow"}

# Mini takvim başlık satırı için kısa gün adları (Pazartesi ilk — datetime.weekday() ile hizalı).
_WEEKDAY_SHORT: dict[str, list[str]] = {
    "tr": ["Pt", "Sa", "Ça", "Pe", "Cu", "Ct", "Pz"],
    "en": ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
}


def weekday_short_labels(lang: str) -> list[str]:
    return _WEEKDAY_SHORT[normalize_language(lang)]


def format_month_year(dt: date, lang: str) -> str:
    """'Ağustos 2026' / 'August 2026' — Takvim ekranının üst başlığı ve mini
    ay takviminin başlığı için (bkz. takvim.html)."""
    lang = normalize_language(lang)
    return f"{month_name(dt.month, lang)} {dt.year}"


def month_name(month: int, lang: str) -> str:
    """1-12 -> ay adı. src/services/timeutil.py::format_date_tr de (CLI'nın
    TR-sabit çağrıları için) bu tabloyu kullanır — iki kopya tutulmuyor."""
    return _MONTH_NAMES[normalize_language(lang)][month - 1]


def format_date(dt: datetime | date, lang: str) -> str:
    """'19 Ağustos 2026' / 'August 19, 2026'."""
    lang = normalize_language(lang)
    month = month_name(dt.month, lang)
    if lang == "en":
        return f"{month} {dt.day}, {dt.year}"
    return f"{dt.day:02d} {month} {dt.year}"


def format_time(dt: datetime, lang: str) -> str:
    """'14:00' — her iki dilde de 24 saatlik format (bkz. modül docstring)."""
    return f"{dt.hour:02d}:{dt.minute:02d}"


def format_datetime(dt: datetime, lang: str) -> str:
    return f"{format_date(dt, lang)}, {format_time(dt, lang)}"


def format_time_range(start: datetime, end: datetime, lang: str) -> str:
    """'14:00–15:00'."""
    return f"{format_time(start, lang)}–{format_time(end, lang)}"


def format_day_header(dt: datetime | date, lang: str) -> str:
    """'Çarşamba, 19 Ağustos' / 'Wednesday, August 19'."""
    lang = normalize_language(lang)
    weekday = _WEEKDAY_NAMES[lang][dt.weekday()]
    month = _MONTH_NAMES[lang][dt.month - 1]
    if lang == "en":
        return f"{weekday}, {month} {dt.day}"
    return f"{weekday}, {dt.day:02d} {month}"


def format_relative_day(dt: date, today: date, lang: str) -> str | None:
    """Bugün/yarın ise çevrilmiş etiketi, değilse None döner (çağıran normal
    tarih biçimine düşer)."""
    lang = normalize_language(lang)
    delta = (dt - today).days
    if delta == 0:
        return _TODAY_KEY[lang]
    if delta == 1:
        return _TOMORROW_KEY[lang]
    return None


def to_display_timezone(dt: datetime, tz_name: str) -> datetime:
    """Aware bir datetime'ı gösterim zaman dilimine çevirir. Naive girişte
    dokunmadan döner (çağıran zaten ensure_timezone ile aware hale getirmeli
    — bkz. src/services/timeutil.py)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(ZoneInfo(tz_name))
