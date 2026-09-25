"""Docker/VPS dağıtımı için eklenen küçük davranışlar: /saglik, MS_REDIRECT_URI."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.ui.app import app
from src.ui.outlook_oauth_routes import _redirect_uri


def test_health_endpoint_needs_no_login_and_reports_ok():
    response = TestClient(app).get("/saglik", follow_redirects=False)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_reports_503_when_db_unavailable(monkeypatch):
    import src.storage.db as db

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "get_connection", _boom)
    response = TestClient(app).get("/saglik", follow_redirects=False)
    assert response.status_code == 503


def test_outlook_redirect_uri_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("MS_REDIRECT_URI", raising=False)
    assert _redirect_uri() == "http://localhost:8000/hesap-ekle-outlook/callback"


def test_outlook_redirect_uri_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("MS_REDIRECT_URI", "https://asistan.example.com/hesap-ekle-outlook/callback")
    assert _redirect_uri() == "https://asistan.example.com/hesap-ekle-outlook/callback"


def test_google_login_without_client_file_returns_to_giris_with_error(monkeypatch, tmp_path):
    import src.ui.oauth_routes as oauth_routes

    monkeypatch.setattr(oauth_routes, "DEFAULT_CLIENT_SECRET_PATH", tmp_path / "yok.json")
    response = TestClient(app).get("/hesaplar/baglan?niyet=giris", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/giris?oauth_hata=client_yok"


def test_giris_page_shows_known_oauth_error_and_ignores_unknown():
    client = TestClient(app)
    shown = client.get("/giris?oauth_hata=client_yok")
    assert shown.status_code == 200
    assert "google_oauth_client.json" in shown.text
    unknown = client.get("/giris?oauth_hata=<script>x</script>")
    assert "<script>x</script>" not in unknown.text
    assert 'role="alert"' not in unknown.text
