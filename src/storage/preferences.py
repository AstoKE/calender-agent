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


def get_preference(key: str, default: Any = None) -> Any:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM user_preferences WHERE preference_key = ?", (key,)
        ).fetchone()
    return json.loads(row["value"]) if row else default


def set_preference(key: str, value: Any, derived_from: str = "manual") -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("DELETE FROM user_preferences WHERE preference_key = ?", (key,))
        conn.execute(
            "INSERT INTO user_preferences (id, preference_key, value, derived_from, approved_by_user, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (str(uuid.uuid4()), key, json.dumps(value), derived_from, now),
        )


def list_preferences() -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT preference_key, value FROM user_preferences").fetchall()
    return {row["preference_key"]: json.loads(row["value"]) for row in rows}
