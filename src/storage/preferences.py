"""Genel amaçlı key/value tercih deposu (`user_preferences` tablosu) —
hesaba BAĞLI OLMAYAN, kalıcı ayarlar için. Hesaba bağlı tercihler (dil/saat
dilimi) için bkz. src/localization/preferences.py (`localization_preferences`,
FK ile account'a bağlı).

Bugün tek kullanım yeri: dil çözümlemesinin (src/ui/session.py::resolve_language)
son kalıcı katmanı — hiç hesap yokken (localization_preferences'a FK nedeniyle
yazılamıyor) dil tercihinin cookie silinse bile hayatta kalması için.

`user_preferences.preference_key` üzerinde UNIQUE YOK — ON CONFLICT
kullanılamaz. Tek transaction içinde DELETE+INSERT tercih edildi: unique
index eklemek ayrı bir migration mekanizması gerektirir ve mevcut mükerrer
satır varsa patlar (bkz. plan Faz 9 kararı) — bu dilim için riske değmez."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.db import get_connection


def get_preference(key: str, default: Any = None, user_id: str | None = None) -> Any:
    """`user_id` verilirse yalnızca o kullanıcıya ait satır (bkz. src/ui/auth.py
    — Web UI'nin login katmanı); `None` ise (CLI — BİLEREK login sisteminin
    dışında, bkz. plan "Real login") ESKİ, sahiplikten bağımsız davranış:
    hangi kullanıcıya ait olursa olsun bu anahtardaki satır. Web login
    katmanı eklendikten SONRA bile CLI'nın çalışmaya devam etmesi için
    (ilk girişte mevcut satırlar o kullanıcıya devrediliyor, bkz.
    auth.py::adopt_orphaned_data — CLI bunu hiç bilmiyor, DB'nin tamamını
    hâlâ "tek kullanıcı" gibi okuyor)."""
    with get_connection() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT value FROM user_preferences WHERE preference_key = ? AND user_id = ?", (key, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT value FROM user_preferences WHERE preference_key = ?", (key,)
            ).fetchone()
    return json.loads(row["value"]) if row else default


def set_preference(key: str, value: Any, derived_from: str = "manual", user_id: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        if user_id is not None:
            conn.execute("DELETE FROM user_preferences WHERE preference_key = ? AND user_id = ?", (key, user_id))
        else:
            conn.execute("DELETE FROM user_preferences WHERE preference_key = ?", (key,))
        conn.execute(
            "INSERT INTO user_preferences (id, preference_key, value, derived_from, approved_by_user, created_at, user_id) "
            "VALUES (?,?,?,?,1,?,?)",
            (str(uuid.uuid4()), key, json.dumps(value), derived_from, now, user_id),
        )


def list_preferences(user_id: str | None = None) -> dict:
    with get_connection() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT preference_key, value FROM user_preferences WHERE user_id = ?", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT preference_key, value FROM user_preferences").fetchall()
    return {row["preference_key"]: json.loads(row["value"]) for row in rows}
