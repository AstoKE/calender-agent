"""Zaman dilimi yardımcıları (bkz. docs/architecture-plan.md §15 Locale-aware tarih çözümleme)."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from src.localization.formatting import month_name

DEFAULT_TIMEZONE = "Europe/Istanbul"  # MVP basitleştirmesi; bkz. localization_preferences tablosu


def format_date_tr(dt: datetime) -> str:
    """CLI'nın (vertical_prototype.py) TR-sabit çağrıları için ince bir shim —
    ay adı tablosu artık src/localization/formatting.py'de (Web UI'nin
    çok dilli format_date'iyle paylaşılıyor, iki kopya tutulmuyor).
    `%d %B` locale'e bağımlı olduğu için (İngilizce ay adı çıktı canlı testte
    görüldü) elle yazılmış tablo kullanılıyor, `locale.setlocale` değil."""
    return f"{dt.day:02d} {month_name(dt.month, 'tr')}"


_CLOCK_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})")
_HOUR_ONLY_RE = re.compile(r"\b(\d{1,2})\b")
_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def parse_clock_time(raw: str) -> str | None:
    """Kullanıcının doğal biçimde yazdığı saati ('11.00 da', '13:00', 'saat 9')
    'HH:MM' biçimine çevirir. Canlı testte kesin 'HH:MM' beklentisinin ('11.00
    da' girdisiyle) çökmeye yol açtığı görüldü — burada esnek ayrıştırılır,
    olmazsa None döner (kullanıcı tekrar denemeye yönlendirilir, çökmez)."""
    raw = raw.strip()
    m = _CLOCK_TIME_RE.search(raw)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m2 = _HOUR_ONLY_RE.search(raw)
        if not m2:
            return None
        hour, minute = int(m2.group(1)), 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_duration_minutes(raw: str) -> int | None:
    """'90', '1 saat', '1.5 saat', '30 dakika' gibi ifadeleri dakikaya çevirir."""
    raw = raw.strip().lower()
    if not raw:
        return None
    m = _NUMBER_RE.search(raw)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value * 60) if "saat" in raw else round(value)


def ensure_timezone(dt: datetime | str | None, tz_name: str = DEFAULT_TIMEZONE) -> datetime | None:
    """LLM çıktısı bazen zaman dilimi içermeyen (naive) bir datetime döndürür;
    Google Calendar API ise RFC3339 + offset zorunlu tutar (naive değer "400
    Bad Request" ile reddedilir — canlı testte görüldü). Naive değerleri
    uygulamanın varsayılan zaman dilimine göre timezone-aware yapar; zaten
    aware olan değerlere dokunmaz. ISO string de kabul eder (çağıranların
    string'i önce datetime'a çevirmesini beklemek, canlı testte bir string'in
    doğrudan buraya verilip .tzinfo erişiminde çökmesine yol açmıştı)."""
    if dt is None or dt == "":
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=ZoneInfo(tz_name))
