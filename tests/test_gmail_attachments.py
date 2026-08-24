"""src/connectors/gmail.py'nin ek dosya (foto/PDF) meta veri çıkarımı için
testler — gerçek Gmail API'ye dokunmuyor, yalnızca payload dict'i işleyen
saf `_extract_attachment_parts` fonksiyonunu test ediyor."""

from __future__ import annotations

from src.connectors.gmail import _extract_attachment_parts


def test_no_parts_returns_empty():
    assert _extract_attachment_parts({}) == []


def test_single_attachment_part_found():
    payload = {
        "parts": [
            {"filename": "davetiye.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1", "size": 1234}},
        ]
    }
    found = _extract_attachment_parts(payload)
    assert len(found) == 1
    assert found[0]["filename"] == "davetiye.pdf"


def test_inline_part_without_filename_is_not_an_attachment():
    payload = {
        "parts": [
            {"filename": "", "mimeType": "text/plain", "body": {"data": "abc"}},
            {"mimeType": "image/png", "body": {"attachmentId": "att1"}},  # filename hiç yok
        ]
    }
    assert _extract_attachment_parts(payload) == []


def test_part_with_filename_but_no_attachment_id_is_not_an_attachment():
    # Bazı inline görseller filename taşıyabilir ama attachmentId yoksa (örn.
    # gövdeye gömülü küçük bir veri) gerçek bir indirilecek ek değildir.
    payload = {"parts": [{"filename": "imza.png", "mimeType": "image/png", "body": {"data": "abc"}}]}
    assert _extract_attachment_parts(payload) == []


def test_nested_multipart_attachment_found_via_recursion():
    payload = {
        "parts": [
            {
                "mimeType": "multipart/mixed",
                "parts": [
                    {"filename": "bilet.jpg", "mimeType": "image/jpeg", "body": {"attachmentId": "att2", "size": 5000}},
                ],
            },
        ]
    }
    found = _extract_attachment_parts(payload)
    assert len(found) == 1
    assert found[0]["filename"] == "bilet.jpg"


def test_multiple_attachments_all_found():
    payload = {
        "parts": [
            {"filename": "a.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1"}},
            {"filename": "b.jpg", "mimeType": "image/jpeg", "body": {"attachmentId": "att2"}},
        ]
    }
    found = _extract_attachment_parts(payload)
    assert {p["filename"] for p in found} == {"a.pdf", "b.jpg"}
