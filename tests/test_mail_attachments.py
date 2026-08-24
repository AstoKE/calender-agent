"""src/services/mail_analysis.py::extract_candidate_from_email_with_attachments
için testler — gerçek Gmail/Gemini API'ye hiç dokunmuyor."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.core.models import Attachment, CandidateStatus, EventType, SourceType, UnifiedEmail
from src.providers.base import FileInputCapable, LLMProvider
from src.services.mail_analysis import extract_candidate_from_email_with_attachments

_EVENT_JSON = json.dumps({
    "event_type": "meeting", "title": "Proje Toplantısı", "start_datetime": "2026-09-01T14:00:00",
    "duration_minutes": 60, "location": None, "ambiguous_fields": [],
})


class _ScriptedVisionLLM(LLMProvider, FileInputCapable):
    def __init__(self, file_response: str = _EVENT_JSON):
        self._file_response = file_response
        self.file_calls: list[tuple[bytes, str]] = []

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}"

    def generate_from_file(self, system_prompt, user_prompt, file_bytes, mime_type, json_output=False):
        self.file_calls.append((file_bytes, mime_type))
        return self._file_response

    def is_available(self):
        return True


class _TextOnlyLLM(LLMProvider):
    """FileInputCapable UYGULAMIYOR — varsayılan Foundry Local'ı temsil eder."""

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}"

    def is_available(self):
        return True


class _FakeGmailConnector:
    def __init__(self, bytes_by_attachment: dict[str, bytes]):
        self._bytes = bytes_by_attachment
        self.download_calls: list[tuple[str, str]] = []

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        self.download_calls.append((message_id, attachment_id))
        return self._bytes[attachment_id]


def _email(attachments=None, body_text="") -> UnifiedEmail:
    return UnifiedEmail(
        provider="gmail",
        account_id="acc1",
        message_id="msg-1",
        thread_id="thread-1",
        subject="Davetiye",
        sender="alerts@example.com",
        recipients=[],
        received_at=datetime.now(timezone.utc),
        body_text=body_text,
        attachments=attachments or [],
    )


def test_returns_none_when_llm_not_file_input_capable():
    email = _email(attachments=[Attachment(filename="davetiye.jpg", content_type="image/jpeg", attachment_id="att1")])
    gmail = _FakeGmailConnector({"att1": b"FAKE_JPEG"})
    assert extract_candidate_from_email_with_attachments(_TextOnlyLLM(), email, gmail) is None


def test_returns_none_when_no_attachments():
    email = _email(attachments=[])
    gmail = _FakeGmailConnector({})
    llm = _ScriptedVisionLLM()
    assert extract_candidate_from_email_with_attachments(llm, email, gmail) is None
    assert llm.file_calls == []


def test_returns_none_when_attachment_mime_type_not_accepted():
    email = _email(attachments=[Attachment(filename="notlar.txt", content_type="text/plain", attachment_id="att1")])
    gmail = _FakeGmailConnector({"att1": b"plain text"})
    llm = _ScriptedVisionLLM()
    assert extract_candidate_from_email_with_attachments(llm, email, gmail) is None
    assert llm.file_calls == []


def test_returns_none_when_attachment_has_no_attachment_id():
    # Metadata var ama indirilecek bir kimlik yok (bkz. gmail.py
    # _extract_attachment_parts'ın bu durumu zaten elemesi gerekiyordu, ama
    # savunmacı kontrol burada da var).
    email = _email(attachments=[Attachment(filename="davetiye.jpg", content_type="image/jpeg", attachment_id=None)])
    gmail = _FakeGmailConnector({})
    llm = _ScriptedVisionLLM()
    assert extract_candidate_from_email_with_attachments(llm, email, gmail) is None


def test_extracts_candidate_from_eligible_attachment():
    email = _email(attachments=[Attachment(filename="davetiye.jpg", content_type="image/jpeg", attachment_id="att1")])
    gmail = _FakeGmailConnector({"att1": b"FAKE_JPEG_BYTES"})
    llm = _ScriptedVisionLLM()

    candidate = extract_candidate_from_email_with_attachments(llm, email, gmail)

    assert candidate is not None
    assert candidate.title == "Proje Toplantısı"
    assert candidate.event_type == EventType.MEETING
    assert candidate.source_type == SourceType.EMAIL
    assert candidate.status == CandidateStatus.READY_FOR_CONFIRMATION
    assert gmail.download_calls == [("msg-1", "att1")]
    assert llm.file_calls == [(b"FAKE_JPEG_BYTES", "image/jpeg")]


def test_disciplined_list_response_uses_first_item():
    email = _email(attachments=[Attachment(filename="program.pdf", content_type="application/pdf", attachment_id="att1")])
    gmail = _FakeGmailConnector({"att1": b"FAKE_PDF"})
    list_response = json.dumps([
        json.loads(_EVENT_JSON),
        {"event_type": "meeting", "title": "İkinci Etkinlik", "start_datetime": "2026-09-02T10:00:00",
         "duration_minutes": 30, "location": None, "ambiguous_fields": []},
    ])
    llm = _ScriptedVisionLLM(file_response=list_response)

    candidate = extract_candidate_from_email_with_attachments(llm, email, gmail)

    assert candidate is not None
    assert candidate.title == "Proje Toplantısı"  # yalnızca ilki alınır, tek candidate bekleniyor
