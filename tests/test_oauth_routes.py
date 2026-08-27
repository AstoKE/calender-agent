"""`/hesaplar/baglan` + `/hesaplar/oauth/geri-don` için ince TestClient
testleri (bkz. plan "Tarayıcıda hesap ekleme"). Gerçek Google OAuth/Gmail API
hiç çağrılmaz — `google_auth_oauthlib.flow.Flow` ve `googleapiclient.discovery.build`
`src.ui.oauth_routes` içindeki isimleriyle sahtelerle değiştiriliyor."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.providers.base import EmbeddingProvider, LLMProvider
from src.storage.db import get_connection

from conftest import login_test_client


class _DummyLLMProvider(LLMProvider):
    def __init__(self, *args, **kwargs):
        pass

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}" if json_output else ""

    def is_available(self):
        return True


class _DummyEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *args, **kwargs):
        pass

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    @property
    def model_name(self):
        return "dummy-embedding"

    @property
    def dimension(self):
        return 8


class _FakeCredentials:
    def to_json(self):
        return '{"token": "fake-token"}'


class _FakeFlow:
    fetch_token_error: Exception | None = None

    def __init__(self, code_verifier=None):
        self.credentials = _FakeCredentials()
        self.code_verifier = code_verifier

    @classmethod
    def from_client_secrets_file(cls, path, scopes=None, redirect_uri=None, state=None, code_verifier=None):
        return cls(code_verifier=code_verifier)

    def authorization_url(self, **kwargs):
        # Gerçek Flow.authorization_url() PKCE code_verifier'ı BURADA üretir
        # (bkz. src/ui/oauth_routes.py'nin en üstündeki "CANLI TESTTE
        # BULUNAN GERÇEK HATA" notu) — sahte de aynı zamanlamayı taklit
        # ediyor ki oauth_routes.py'nin `flow.code_verifier`'ı cookie'ye
        # yazma satırı gerçek kodla aynı şekilde çalışsın.
        self.code_verifier = "fake-code-verifier"
        return "https://accounts.google.com/fake-consent-screen", "fake-state-123"

    def fetch_token(self, code=None):
        if _FakeFlow.fetch_token_error is not None:
            raise _FakeFlow.fetch_token_error


class _FakeGmailService:
    email = "newuser@example.com"

    def users(self):
        return self

    def getProfile(self, userId):  # noqa: N802 — Google API'nin kendi ismi
        return self

    def execute(self):
        return {"emailAddress": _FakeGmailService.email}


def _fake_build(service_name, version, credentials=None):
    return _FakeGmailService()


@pytest.fixture
def client(temp_db, monkeypatch, tmp_path):
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.oauth_routes.Flow", _FakeFlow)
    monkeypatch.setattr("src.ui.oauth_routes.build", _fake_build)

    client_secret_file = tmp_path / "google_oauth_client.json"
    client_secret_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("src.ui.oauth_routes.DEFAULT_CLIENT_SECRET_PATH", client_secret_file)

    _FakeFlow.fetch_token_error = None
    _FakeGmailService.email = "newuser@example.com"

    from src.ui.app import app

    with TestClient(app) as test_client:
        # Bu dosyadaki testler "hesap ekle" akışını (zaten giriş yapılmış,
        # EK bir Gmail hesabı bağlanıyor) doğruluyor — "giriş yap" akışının
        # kendisi (niyet=giris) AYRI testlerde (bkz. altta), login OLMAMIŞ
        # bir client ile doğrulanıyor.
        login_test_client(test_client)
        yield test_client


@pytest.fixture
def anonymous_client(temp_db, monkeypatch, tmp_path):
    """`client` ile AYNI sahtelerle ama BİLEREK giriş yapılmamış — /giris
    akışının kendisini (niyet=giris) test etmek için."""
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.oauth_routes.Flow", _FakeFlow)
    monkeypatch.setattr("src.ui.oauth_routes.build", _fake_build)

    client_secret_file = tmp_path / "google_oauth_client.json"
    client_secret_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("src.ui.oauth_routes.DEFAULT_CLIENT_SECRET_PATH", client_secret_file)

    _FakeFlow.fetch_token_error = None
    _FakeGmailService.email = "newuser@example.com"

    from src.ui.app import app

    with TestClient(app) as test_client:
        yield test_client


def test_start_oauth_redirects_to_google_and_sets_state_cookie(client):
    response = client.get("/hesaplar/baglan", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://accounts.google.com/fake-consent-screen"
    assert response.cookies.get("oauth_state") == "fake-state-123"
    # PKCE code_verifier de taşınmalı — bkz. modülün en üstündeki "CANLI
    # TESTTE BULUNAN GERÇEK HATA" notu (Google flow'u callback'te YENİDEN
    # kuruluyor, code_verifier aktarılmazsa "Missing code verifier" ile patlıyordu).
    assert response.cookies.get("oauth_verifier") == "fake-code-verifier"


def test_start_oauth_missing_client_file_redirects_with_error(client, monkeypatch, tmp_path):
    monkeypatch.setattr("src.ui.oauth_routes.DEFAULT_CLIENT_SECRET_PATH", tmp_path / "olmayan.json")
    response = client.get("/hesaplar/baglan", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?oauth_hata=client_yok"


def test_callback_user_denied_consent_redirects_with_error(client):
    response = client.get("/hesaplar/oauth/geri-don?error=access_denied", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?oauth_hata=reddedildi"


def test_callback_missing_state_redirects_with_error(client):
    response = client.get("/hesaplar/oauth/geri-don?code=abc", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?oauth_hata=gecersiz"


def test_callback_state_mismatch_redirects_with_error(client):
    response = client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=baska-bir-state",
        cookies={"oauth_state": "fake-state-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?oauth_hata=gecersiz"


def test_callback_success_registers_account_and_sets_active_cookie(client):
    response = client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?hesap_eklendi=1"
    assert response.cookies.get("active_account") == "newuser"

    with get_connection() as conn:
        row = conn.execute("SELECT email, provider, status FROM accounts WHERE id = ?", ("newuser",)).fetchone()
    assert row["email"] == "newuser@example.com"
    assert row["provider"] == "google"
    assert row["status"] == "active"


def test_callback_writes_token_file(client, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("src.connectors.google_auth.DATA_DIR", data_dir)

    response = client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    token_file = data_dir / "google_token_newuser.json"
    assert token_file.exists()
    assert token_file.read_text(encoding="utf-8") == '{"token": "fake-token"}'


def test_callback_exception_during_token_exchange_redirects_with_error(client):
    _FakeFlow.fetch_token_error = RuntimeError("boom")
    response = client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/hesaplar?oauth_hata=basarisiz"


def test_connect_without_login_redirects_to_giris_not_registered(anonymous_client):
    # /hesaplar/baglan (niyet=giris OLMADAN) giriş yapılmamış bir ziyaretçi
    # tarafından çağrılırsa (bkz. app.py auth_guard_middleware'in bu rotayı
    # MUAF tutması — giriş akışının kendisi buradan geçtiği için) sahipsiz
    # bir hesap kaydetmek yerine /giris'e yönlenmeli.
    response = anonymous_client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/giris?oauth_hata=gecersiz"
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_login_start_sets_intent_cookie(anonymous_client):
    response = anonymous_client.get("/hesaplar/baglan?niyet=giris", follow_redirects=False)
    assert response.status_code == 302
    assert response.cookies.get("oauth_intent") == "giris"


def test_login_first_time_creates_user_and_session(anonymous_client):
    response = anonymous_client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123", "oauth_intent": "giris"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/anasayfa"
    assert response.cookies.get("session_token")

    with get_connection() as conn:
        user_row = conn.execute("SELECT id, email FROM users WHERE email = ?", ("newuser@example.com",)).fetchone()
        assert user_row is not None
        account_row = conn.execute("SELECT user_id FROM accounts WHERE id = ?", ("newuser",)).fetchone()
        assert account_row["user_id"] == user_row["id"]


def test_login_adopts_previously_orphaned_accounts(anonymous_client):
    from src.connectors.account_registry import ensure_account_registered

    # Bu login sisteminden ÖNCE (ya da CLI'dan) eklenmiş, sahipsiz bir hesap.
    ensure_account_registered("eskihesap", provider="google", email="eski@example.com")

    response = anonymous_client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123", "oauth_intent": "giris"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with get_connection() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("newuser@example.com",)).fetchone()[0]
        adopted = conn.execute("SELECT user_id FROM accounts WHERE id = 'eskihesap'").fetchone()[0]
    assert adopted == user_id


def test_login_second_time_reuses_existing_user_no_duplicate(anonymous_client):
    first = anonymous_client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123", "oauth_intent": "giris"},
        follow_redirects=False,
    )
    anonymous_client.cookies.clear()
    second = anonymous_client.get(
        "/hesaplar/oauth/geri-don?code=abc&state=fake-state-123",
        cookies={"oauth_state": "fake-state-123", "oauth_intent": "giris"},
        follow_redirects=False,
    )
    assert first.status_code == second.status_code == 303
    assert first.cookies.get("session_token") != second.cookies.get("session_token")

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE email = ?", ("newuser@example.com",)).fetchone()[0]
    assert count == 1

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert count == 1  # ensure_account_registered idempotent — ikinci girişte yeni satır eklenmez
