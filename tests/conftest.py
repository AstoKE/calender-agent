import logging
import sys

import dotenv
import pytest

from _isolation import (
    COPIED_PATH_CONSTANTS,
    CREDENTIAL_ENV_VARS,
    block_native_model,
    block_network,
)
from src.storage import db as db_module


def pytest_configure(config):
    # Runs before test-module collection: app.py loads .env at import time,
    # so function-scoped fixtures alone are too late to prevent that read.
    guard = pytest.MonkeyPatch()
    config.add_cleanup(guard.undo)
    guard.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    for name in CREDENTIAL_ENV_VARS:
        guard.delenv(name, raising=False)
    block_network(guard)
    from src.providers import foundry_local
    guard.setattr(foundry_local, "_get_manager", block_native_model)


@pytest.fixture(autouse=True)
def _no_real_cloud_llm(monkeypatch):
    """Testler ASLA gerçek bir bulut LLM çağrısı yapmamalı — geliştiricinin
    kendi `.env`'inde `LLM_PROVIDER=gemini`/gerçek bir API key olsa bile
    (bkz. src/ui/app.py, src/providers/gemini.py) test ortamında bu her
    zaman etkisiz kılınır. `temp_db`'nin gerçek DB'yi koruması gibi, bu da
    gerçek/ücretli bir dış servise yanlışlıkla istek gitmesini engelliyor."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Testler gerçek data/calendar_agent.db'ye ASLA dokunmaz — her test kendi
    geçici SQLite dosyasını kullanır (bkz. db.py'deki None-sentinel düzeltmesi,
    monkeypatch olmadan bu izolasyon çalışmazdı)."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", path)
    db_module.init_db()
    return path


@pytest.fixture(autouse=True)
def isolated_files(temp_db, tmp_path, monkeypatch):
    """Keep OAuth, logs and copied UI path constants inside this test's directory."""
    from src.connectors import google_auth, microsoft_auth
    from src.core import logging_config

    data_dir = tmp_path / "isolated_runtime"
    data_dir.mkdir()
    client_path = data_dir / "google_oauth_client.json"
    log_path = data_dir / "debug.log"
    monkeypatch.setattr(google_auth, "DATA_DIR", data_dir)
    monkeypatch.setattr(microsoft_auth, "DATA_DIR", data_dir)
    monkeypatch.setattr(google_auth, "DEFAULT_CLIENT_SECRET_PATH", client_path)
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    monkeypatch.setattr(logging_config, "_configured", False)
    # Some modules copy these constants by value at import time; patching only
    # the defining module would leave those copies on the real path. The
    # declaration lives in _isolation.py so a test can check it stays complete.
    isolated_values = {
        "DATA_DIR": data_dir,
        "DEFAULT_CLIENT_SECRET_PATH": client_path,
        "DEFAULT_DB_PATH": temp_db,
        "LOG_PATH": log_path,
    }
    for module_name, names in COPIED_PATH_CONSTANTS.items():
        module = sys.modules.get(module_name)
        if module is not None:
            for name in names:
                monkeypatch.setattr(module, name, isolated_values[name])

    logger = logging.getLogger("calendar_agent")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    try:
        yield data_dir
    finally:
        for handler in list(logger.handlers):
            if handler not in previous_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(previous_level)


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
    # AUTH-03 (bkz. src/ui/security.py): CSRFGuardMiddleware artık Fetch
    # Metadata başlığı hiç yoksa isteği REDDEDİYOR (fail-closed) — httpx
    # tabanlı TestClient, gerçek bir tarayıcının aksine bu başlığı kendiliğinden
    # eklemiyor. Testlerin kendisi CSRF'i konu almadığı sürece (bkz.
    # test_ui_routes.py'nin kendi cross-site/same-origin testleri, kendi
    # başlıklarını AÇIKÇA veriyor ve bu varsayılanı ezer) gerçek bir aynı-
    # sekme tarayıcı isteğini simüle etmek için istemci seviyesinde
    # varsayılan bir "same-origin" başlığı ayarlanıyor.
    test_client.headers["sec-fetch-site"] = "same-origin"
    return user
