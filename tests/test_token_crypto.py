"""PRIV-01 (token yarısı, bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md):
OAuth token dosyalarının isteğe bağlı şifrelenmesi — src/core/token_crypto.py
ve google_auth.py/microsoft_auth.py'nin bunu kullanan okuma/yazma yolları.
Gerçek Gmail/Outlook API'sine hiç dokunulmuyor."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from src.connectors import google_auth, microsoft_auth
from src.core import token_crypto


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    return key


# --- token_crypto.py çekirdek davranışı ---


def test_no_key_passes_plaintext_through_unchanged():
    assert token_crypto.encrypt_for_storage('{"a": 1}') == '{"a": 1}'
    assert token_crypto.decrypt_from_storage('{"a": 1}') == '{"a": 1}'


def test_round_trips_with_a_key(encryption_key):
    original = '{"refresh_token": "GIZLI-DEGER-abc123"}'
    encrypted = token_crypto.encrypt_for_storage(original)
    assert encrypted != original
    assert "GIZLI-DEGER-abc123" not in encrypted
    assert token_crypto.decrypt_from_storage(encrypted) == original


def test_decrypt_falls_back_to_plaintext_for_legacy_unencrypted_data(encryption_key):
    # Bir anahtar SONRADAN ayarlanırsa, henüz şifrelenmemiş eski dosyalar hâlâ
    # okunabilmeli (otomatik geçiş — bkz. modül docstring'i).
    legacy_plaintext = '{"refresh_token": "eski-duz-metin"}'
    assert token_crypto.decrypt_from_storage(legacy_plaintext) == legacy_plaintext


def test_invalid_key_raises_a_clear_error(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        token_crypto.encrypt_for_storage("x")


# --- google_auth.py entegrasyonu ---


def test_google_save_writes_plaintext_when_no_key(isolated_files):
    creds = Mock()
    creds.to_json.return_value = '{"token": "plain"}'
    google_auth.save_credentials_for_account("acc1", creds)
    assert (isolated_files / "google_token_acc1.json").read_text() == '{"token": "plain"}'


def test_google_save_writes_encrypted_when_key_set(isolated_files, encryption_key):
    creds = Mock()
    creds.to_json.return_value = '{"token": "SIR-DEGER"}'
    google_auth.save_credentials_for_account("acc1", creds)
    on_disk = (isolated_files / "google_token_acc1.json").read_text()
    assert "SIR-DEGER" not in on_disk


def test_google_reads_back_its_own_encrypted_file(isolated_files, encryption_key, monkeypatch):
    creds = Mock()
    creds.to_json.return_value = '{"token": "abc", "refresh_token": "r", "client_id": "c", "client_secret": "s"}'
    google_auth.save_credentials_for_account("acc1", creds)

    captured = {}
    monkeypatch.setattr(
        google_auth.Credentials, "from_authorized_user_info",
        classmethod(lambda cls, info, scopes=None: captured.setdefault("info", info)),
    )
    google_auth._load_credentials_from_file(google_auth._token_path("acc1"), scopes=[])
    assert captured["info"]["token"] == "abc"


def test_google_reads_legacy_plaintext_file_after_key_is_introduced(isolated_files, encryption_key, monkeypatch):
    # Anahtar YOKKEN yazılmış eski bir dosya, anahtar SONRADAN eklenince de
    # okunabilmeli (bkz. token_crypto.decrypt_from_storage docstring'i).
    token_path = google_auth._token_path("acc1")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text('{"token": "eski-duz-metin"}', encoding="utf-8")

    captured = {}
    monkeypatch.setattr(
        google_auth.Credentials, "from_authorized_user_info",
        classmethod(lambda cls, info, scopes=None: captured.setdefault("info", info)),
    )
    google_auth._load_credentials_from_file(token_path, scopes=[])
    assert captured["info"]["token"] == "eski-duz-metin"


# --- microsoft_auth.py entegrasyonu ---


def test_ms_save_writes_plaintext_when_no_key(isolated_files):
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "plain-cache"
    microsoft_auth.save_ms_token_cache("acc1", cache)
    assert (isolated_files / "ms_token_acc1.json").read_text() == "plain-cache"


def test_ms_save_writes_encrypted_when_key_set(isolated_files, encryption_key):
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = '{"marker": "SIR-CACHE-DEGERI"}'
    microsoft_auth.save_ms_token_cache("acc1", cache)
    on_disk = (isolated_files / "ms_token_acc1.json").read_text()
    assert "SIR-CACHE-DEGERI" not in on_disk


def test_ms_round_trips_through_save_and_load(isolated_files, encryption_key):
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = '{"marker": "gercek-cache-icerigi"}'
    microsoft_auth.save_ms_token_cache("acc1", cache)

    loaded = microsoft_auth.load_ms_token_cache("acc1")  # gerçek msal.SerializableTokenCache

    assert "gercek-cache-icerigi" in loaded.serialize()


def test_ms_reads_legacy_plaintext_cache_after_key_is_introduced(isolated_files, encryption_key):
    token_path = microsoft_auth._token_cache_path("acc1")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text('{"marker": "eski-duz-metin"}', encoding="utf-8")

    cache = microsoft_auth.load_ms_token_cache("acc1")

    assert "eski-duz-metin" in cache.serialize()
