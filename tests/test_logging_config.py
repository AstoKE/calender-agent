"""PRIV-01 (bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md) log yarısı: ham
mail gövdesi/sohbet metni/LLM girdi-çıktısının data/debug.log'a varsayılan
olarak MASKELİ yazıldığını doğrular — hem `redact()`'in kendisini hem de
gerçek çağrı zincirinin (json_generation.py, her LLM çağrısının ortak
boğazı) onu gerçekten kullandığını, dosyaya yazılan satırı okuyarak."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from src.core.logging_config import configure_logging, get_logger, redact
from src.providers.base import LLMProvider
from src.providers.json_generation import generate_json


def test_short_text_passes_through_unchanged():
    text = "kısa bir mail konusu"
    assert redact(text) == text


def test_long_text_is_truncated_and_hashed():
    text = "A" * 200
    result = redact(text)
    assert result.startswith("A" * 80)
    assert "A" * 200 not in result
    assert "120 more chars redacted" in result
    assert "sha256=" in result


def test_redaction_is_deterministic_for_the_same_content():
    text = "gizli mail gövdesi " * 20
    assert redact(text) == redact(text)


def test_different_content_produces_different_hashes():
    a = redact("x" * 200)
    b = redact("y" * 200)
    assert a != b


def test_non_string_input_passed_through(monkeypatch):
    # generate_json call sitelerindeki %s formatlamasıyla çağrılabilecek
    # sözlük/liste gibi değerler redact()'i kırmamalı.
    assert redact(None) is None
    assert redact(42) == 42


def test_log_content_full_disables_redaction(monkeypatch):
    monkeypatch.setenv("LOG_CONTENT", "full")
    text = "B" * 200
    assert redact(text) == text


def test_log_content_full_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("LOG_CONTENT", "FULL")
    text = "C" * 200
    assert redact(text) == text


def test_configure_logging_uses_rotating_file_handler(isolated_files):
    configure_logging()
    logger = logging.getLogger("calendar_agent")
    file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes == 10 * 1024 * 1024
    assert file_handlers[0].backupCount == 5


class _CannedJSONProvider(LLMProvider):
    """generate() çağrısı ne gelirse gelsin sabit bir JSON döner (verilen
    ``payload`` alan değeri olarak) — gerçek bir sağlayıcıya ihtiyaç
    duymadan "modelin ürettiği metin loga nasıl yazılıyor" zincirini uçtan
    uca test eder."""

    def __init__(self, payload: str):
        self._payload = payload

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        import json as _json
        return _json.dumps({"payload": self._payload})

    def is_available(self):
        return True


def test_generate_json_does_not_write_raw_user_prompt_to_the_log_file(isolated_files):
    configure_logging()
    logger = logging.getLogger("calendar_agent.json_generation")
    logger.setLevel(logging.DEBUG)

    sensitive_body = "Gizli toplanti notlari: proje X butcesi 2 milyon TL. " * 10
    secret_marker = "GIZLI-BUTCE-DEGERI-9f3a21"
    user_prompt = sensitive_body + secret_marker

    generate_json(_CannedJSONProvider(payload="ok"), "sistem promptu", user_prompt)

    log_text = (isolated_files / "debug.log").read_text(encoding="utf-8")
    assert secret_marker not in log_text
    assert sensitive_body not in log_text
    # Maskelenmiş biçimde iz bırakmalı — tamamen sessiz değil, ama okunaklı değil.
    assert "more chars redacted" in log_text
    assert "sha256=" in log_text


def test_generate_json_still_logs_the_system_prompt_in_full(isolated_files):
    # system_prompt bizim kendi şablonumuz, kullanıcı içeriği değil —
    # maskelenmemesi hata ayıklama değerini korur.
    configure_logging()
    logger = logging.getLogger("calendar_agent.json_generation")
    logger.setLevel(logging.DEBUG)

    system_prompt = "SISTEM-PROMPT-IZI-abc123"
    generate_json(_CannedJSONProvider(payload="ok"), system_prompt, "kısa mesaj")

    log_text = (isolated_files / "debug.log").read_text(encoding="utf-8")
    assert system_prompt in log_text


def test_generate_json_masks_long_raw_model_output(isolated_files):
    configure_logging()
    logger = logging.getLogger("calendar_agent.json_generation")
    logger.setLevel(logging.DEBUG)

    secret_marker = "MODEL-CIKTISINDAKI-SIR-7e21bc"
    generate_json(_CannedJSONProvider(payload=secret_marker * 5), "sistem", "kısa mesaj")

    log_text = (isolated_files / "debug.log").read_text(encoding="utf-8")
    # Ham JSON çıktısının TAMAMI (secret_marker'ın 5 tekrarı) log dosyasında
    # bulunmamalı — kısa bir önizleme + hash dışında.
    assert (secret_marker * 5) not in log_text


def test_get_logger_returns_namespaced_logger():
    assert get_logger("probe").name == "calendar_agent.probe"
