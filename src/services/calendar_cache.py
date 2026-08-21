"""Google Calendar `list_events()` için kısa ömürlü, write-through bir cache
(bkz. proje roadmap'inin 2. maddesi: `calendar_events_cache` senkronizasyonu).

Tasarım (kullanıcıyla netleştirildi — geniş/proaktif bir arka plan senkronu
DEĞİL): her canlı `list_events` çağrısı sonucu `calendar_events_cache`'e
yazılır (write-through). Bir sonraki istek AYNI hesap+takvim için gelirse ve
istenen aralık en son senkronize edilen aralığın İÇİNDEYSE ve `CACHE_TTL_SECONDS`
dolmadıysa, canlı API'ye hiç gidilmeden cache'ten sunulur. Aksi halde (aralık
dışı, TTL dolmuş, ya da hiç senkronize edilmemiş) şeffafçe canlıya düşülür —
`list_events_cached` çağıranlar için `calendar.list_events(...)`'in birebir
yerine geçer, davranış hiç değişmez, yalnızca hızlanır.

Tek kullanıcılı yerel bir uygulamada Google'ın API kotası zaten sorun değil —
bu yüzden BİLİNÇLİ olarak geniş bir pencereyi arka planda proaktif senkronize
eden bir tasarım seçilmedi (gerçek fayda/karmaşıklık oranı düşük olurdu).
`calendar_sync_state` tablosu (account_id, calendar_id) başına TEK satır tutar
— haftadan haftaya gezinme gibi farklı aralıklar istendiğinde bu satır o yeni
aralıkla DEĞİŞTİRİLİR (genişletilmez/birleştirilmez), bu yüzden cache asıl
faydayı aynı sayfanın art arda yeniden yüklenmesinde / birbirine yakın zamanda
örtüşen isteklerde (örn. Ana Sayfa + Takvim aynı hafta) sağlar.

`calendar_events_cache.raw_json`, Google'ın döndürdüğü HAM event dict'ini
olduğu gibi saklar — `parse_google_event`'in beklediği tam şekli (status,
start.date/dateTime, htmlLink, ...) kayıpsız yeniden üretmek için; tablonun
title/start_datetime/... sütunları yalnızca SQL tarafında aralık filtrelemesi
için var, cache'ten okurken kaynak olarak raw_json kullanılıyor."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from src.storage.db import get_connection

CACHE_TTL_SECONDS = 120


def get_cached_events(account_id: str, calendar_id: str, time_min: datetime, time_max: datetime) -> list[dict] | None:
    """Cache TAZE ve istenen aralığı tamamen kapsıyorsa ham event dict'lerini
    döner; aksi halde None (çağıran canlıya düşmeli)."""
    with get_connection() as conn:
        sync_row = conn.execute(
            "SELECT range_start, range_end, synced_at FROM calendar_sync_state "
            "WHERE account_id = ? AND calendar_id = ?",
            (account_id, calendar_id),
        ).fetchone()
        if sync_row is None:
            return None

        synced_at = datetime.fromisoformat(sync_row["synced_at"])
        if datetime.now(timezone.utc) - synced_at > timedelta(seconds=CACHE_TTL_SECONDS):
            return None

        cached_start = datetime.fromisoformat(sync_row["range_start"])
        cached_end = datetime.fromisoformat(sync_row["range_end"])
        if not (cached_start <= time_min and time_max <= cached_end):
            return None

        rows = conn.execute(
            "SELECT raw_json FROM calendar_events_cache WHERE account_id = ? AND calendar_id = ?",
            (account_id, calendar_id),
        ).fetchall()
    return [json.loads(row["raw_json"]) for row in rows if row["raw_json"]]


def store_events(
    account_id: str, calendar_id: str, time_min: datetime, time_max: datetime, raw_events: list[dict]
) -> None:
    """Canlı bir `list_events` sonucunu write-through olarak cache'e yazar.
    Bu (account_id, calendar_id) için önceki cache içeriğinin TAMAMEN yerine
    geçer (kısmi aralık birleştirme YAPILMAZ — bkz. modül docstring'i);
    `calendar_sync_state` tek satırı da bu yeni aralıkla güncellenir."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM calendar_events_cache WHERE account_id = ? AND calendar_id = ?",
            (account_id, calendar_id),
        )
        for raw in raw_events:
            start_raw = raw.get("start") or {}
            end_raw = raw.get("end") or {}
            conn.execute(
                """
                INSERT INTO calendar_events_cache (
                    id, account_id, provider, calendar_id, event_id, title,
                    start_datetime, end_datetime, location, raw_json, last_synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    account_id,
                    "google",
                    calendar_id,
                    raw.get("id", ""),
                    raw.get("summary"),
                    start_raw.get("dateTime") or start_raw.get("date"),
                    end_raw.get("dateTime") or end_raw.get("date"),
                    raw.get("location"),
                    json.dumps(raw),
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO calendar_sync_state (account_id, calendar_id, range_start, range_end, synced_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(account_id, calendar_id) DO UPDATE SET
                range_start = excluded.range_start,
                range_end = excluded.range_end,
                synced_at = excluded.synced_at
            """,
            (account_id, calendar_id, time_min.isoformat(), time_max.isoformat(), now),
        )


def list_events_cached(calendar, account_id: str, time_min: datetime, time_max: datetime, calendar_id: str = "primary") -> list[dict]:
    """`calendar.list_events(time_min, time_max)`'in cache-first sarmalayıcısı
    — çağıranlar için birebir yerine geçer (aynı ham Google event dict listesi
    döner), yalnızca cache-hit durumunda canlı API çağrısı atlanır."""
    cached = get_cached_events(account_id, calendar_id, time_min, time_max)
    if cached is not None:
        return cached
    raw_events = calendar.list_events(time_min, time_max, calendar_id)
    store_events(account_id, calendar_id, time_min, time_max, raw_events)
    return raw_events
