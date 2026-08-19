"""src/storage/preferences.py — user_preferences için testler (bkz. plan
Faz 9). Tabloda preference_key üzerinde UNIQUE yok, bu yüzden set_preference
DELETE+INSERT ile upsert simüle ediyor — burada mükerrer satır oluşmadığı
doğrulanıyor."""

from src.storage.db import get_connection
from src.storage.preferences import get_preference, list_preferences, set_preference


def test_get_preference_missing_returns_default(temp_db):
    assert get_preference("ui.language") is None
    assert get_preference("ui.language", "tr") == "tr"


def test_set_and_get_preference(temp_db):
    set_preference("ui.language", "en")
    assert get_preference("ui.language") == "en"


def test_set_preference_upserts_without_duplicate_rows(temp_db):
    set_preference("ui.language", "en")
    set_preference("ui.language", "tr")
    assert get_preference("ui.language") == "tr"

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM user_preferences WHERE preference_key = ?", ("ui.language",)
        ).fetchone()[0]
    assert count == 1


def test_set_preference_stores_complex_values(temp_db):
    set_preference("some.list", [1, 2, 3])
    assert get_preference("some.list") == [1, 2, 3]


def test_list_preferences(temp_db):
    set_preference("ui.language", "en")
    set_preference("other.key", 42)
    assert list_preferences() == {"ui.language": "en", "other.key": 42}
