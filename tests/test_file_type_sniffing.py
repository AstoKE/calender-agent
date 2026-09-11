"""INPUT-01 (bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md): "gerçek dosya
türü doğrulama" — istemcinin/mailin beyan ettiği Content-Type'a değil,
dosyanın GERÇEK ilk baytlarına (sihirli sayı) bakan `sniff_file_mime_type`/
`file_content_matches_declared_type` (src/services/vertical_prototype.py)
ve bunu kullanan `chat_flow.py::_dispatch_file_upload`'ın reddetme yolu."""

from __future__ import annotations

from src.providers.base import EmbeddingProvider, FileInputCapable, LLMProvider
from src.services.chat_flow import _dispatch_file_upload
from src.services.vertical_prototype import file_content_matches_declared_type, sniff_file_mime_type


def test_sniffs_known_signatures():
    assert sniff_file_mime_type(b"%PDF-1.4 rest of file") == "application/pdf"
    assert sniff_file_mime_type(b"\x89PNG\r\n\x1a\n" + b"rest") == "image/png"
    assert sniff_file_mime_type(b"\xff\xd8\xff" + b"rest") == "image/jpeg"
    assert sniff_file_mime_type(b"RIFF____WEBP" + b"rest") == "image/webp"
    assert sniff_file_mime_type(b"____ftypheic" + b"rest") == "image/heic"


def test_sniff_returns_none_for_unrecognized_bytes():
    assert sniff_file_mime_type(b"plain text, not a real file") is None
    assert sniff_file_mime_type(b"") is None


def test_content_matches_declared_type_for_correct_pairing():
    assert file_content_matches_declared_type("application/pdf", b"%PDF-1.4 rest")
    assert file_content_matches_declared_type("image/jpeg", b"\xff\xd8\xff" + b"rest")


def test_heic_and_heif_are_treated_as_the_same_family():
    heic_bytes = b"____ftypheic" + b"rest"
    assert file_content_matches_declared_type("image/heic", heic_bytes)
    assert file_content_matches_declared_type("image/heif", heic_bytes)


def test_content_mismatch_is_rejected():
    # Bir PNG'yi PDF diye beyan etmek — istemci/mail göndereni Content-Type
    # üzerinde tam kontrole sahip, gerçek baytlarla çapraz kontrol şart.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest"
    assert not file_content_matches_declared_type("application/pdf", png_bytes)
    assert not file_content_matches_declared_type("image/jpeg", b"plain text")


class _RaisingVisionLLM(LLMProvider, FileInputCapable):
    """`generate_from_file` çağrılırsa test başarısız olsun diye BİLEREK
    patlıyor — içerik reddi, hiçbir LLM çağrısı yapılmadan (yani boşuna
    ücretli bir API çağrısı tetiklenmeden) gerçekleşmeli."""

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}"

    def generate_from_file(self, system_prompt, user_prompt, file_bytes, mime_type, json_output=False):
        raise AssertionError("generate_from_file çağrılmamalıydı — içerik reddi LLM'den ÖNCE olmalı")

    def is_available(self):
        return True


class _DummyEmbeddingProvider(EmbeddingProvider):
    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    @property
    def model_name(self):
        return "dummy-embedding"

    @property
    def dimension(self):
        return 8


def test_dispatch_file_upload_rejects_spoofed_content_type():
    # Content-Type "image/jpeg" iddia ediyor ama gerçek baytlar bir PNG.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest"
    state, messages = _dispatch_file_upload(
        png_bytes, "image/jpeg", "", llm=_RaisingVisionLLM(), embedding_provider=_DummyEmbeddingProvider(),
        calendar=None, lang="tr",
    )
    assert state.candidate is None
    assert any("desteklenmiyor" in m for m in messages)


def test_dispatch_file_upload_rejects_oversized_even_with_valid_content():
    from src.services.vertical_prototype import MAX_FILE_SIZE_BYTES

    oversized_jpeg = b"\xff\xd8\xff" + b"\x00" * MAX_FILE_SIZE_BYTES  # cap + 3 bayt
    state, messages = _dispatch_file_upload(
        oversized_jpeg, "image/jpeg", "", llm=_RaisingVisionLLM(), embedding_provider=_DummyEmbeddingProvider(),
        calendar=None, lang="tr",
    )
    assert state.candidate is None
    assert any("büyük" in m for m in messages)
