import pytest

from src.storage import db as db_module


@pytest.fixture(autouse=True)
def _no_real_cloud_llm(monkeypatch):
    """Testler ASLA gerçek bir bulut LLM çağrısı yapmamalı — geliştiricinin
    kendi `.env`'inde `LLM_PROVIDER=gemini`/gerçek bir API key olsa bile
    (bkz. src/ui/app.py, src/providers/gemini.py) test ortamında bu her
    zaman etkisiz kılınır. `temp_db`'nin gerçek DB'yi koruması gibi, bu da
    gerçek/ücretli bir dış servise yanlışlıkla istek gitmesini engelliyor."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Testler gerçek data/calendar_agent.db'ye ASLA dokunmaz — her test kendi
    geçici SQLite dosyasını kullanır (bkz. db.py'deki None-sentinel düzeltmesi,
    monkeypatch olmadan bu izolasyon çalışmazdı)."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", path)
    db_module.init_db()
    return path
