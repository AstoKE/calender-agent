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


def login_test_client(test_client, email: str = "test@example.com") -> dict:
    """Bu login sisteminden ÖNCEki testlerin çoğu (bkz. plan "Real login")
    doğrudan `TestClient(app)` kuruyordu — auth_guard_middleware eklendikten
    sonra bu, her istekte /giris'e yönlenmeye yol açar. Testin kendi
    `client` fixture'ı bunu bir kez çağırıp döndürülen cookie'yi
    `test_client`'ın kalıcı cookie jar'ına yazmalı, testler login akışının
    KENDİSİNİ test etmedikçe (bkz. test_oauth_routes.py'nin niyet=giris
    testleri, AYRI bir login-yapmamış client kullanır) auth'u hiç bilmesin
    diye."""
    from src.ui.auth import create_session, create_user

    user = create_user(email)
    token = create_session(user["id"])
    test_client.cookies.set("session_token", token)
    test_client.test_user = user  # testler user_id gerektiğinde (bkz. master takvim hesabı testleri) buradan okur
    return user
