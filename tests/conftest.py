import pytest

from src.storage import db as db_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Testler gerçek data/calendar_agent.db'ye ASLA dokunmaz — her test kendi
    geçici SQLite dosyasını kullanır (bkz. db.py'deki None-sentinel düzeltmesi,
    monkeypatch olmadan bu izolasyon çalışmazdı)."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", path)
    db_module.init_db()
    return path
