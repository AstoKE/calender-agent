"""Zaman dilimi yardımcıları (bkz. docs/architecture-plan.md §15 Locale-aware tarih çözümleme)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Istanbul"  # MVP basitleştirmesi; bkz. localization_preferences tablosu

_TURKISH_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def format_date_tr(dt: datetime) -> str:
    """'%d %B' locale'e (ve dolayısıyla sistem ayarına) bağımlı olduğu için
    (İngilizce ay adı çıktı canlı testte görüldü) Türkçe ay adını elle
    map'ler. Gerçek çok dilli biçimlendirme (kullanıcı dili bazlı) Hafta
    2-3'ün lokalizasyon kapsamına dahil edilecek."""
    return f"{dt.day:02d} {_TURKISH_MONTHS[dt.month - 1]}"


def ensure_timezone(dt: datetime | None, tz_name: str = DEFAULT_TIMEZONE) -> datetime | None:
    """LLM çıktısı bazen zaman dilimi içermeyen (naive) bir datetime döndürür;
    Google Calendar API ise RFC3339 + offset zorunlu tutar (naive değer "400
    Bad Request" ile reddedilir — canlı testte görüldü). Naive değerleri
    uygulamanın varsayılan zaman dilimine göre timezone-aware yapar; zaten
    aware olan değerlere dokunmaz."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=ZoneInfo(tz_name))
