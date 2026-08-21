import json
from datetime import datetime, timezone

from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType, UnifiedEmail
from src.providers.base import LLMProvider
from src.services.mail_analysis import analyze_possible_update


class _ScriptedLLM(LLMProvider):
    """analyze_possible_update yalnızca generate()'i (json_output=True) çağırır
    — gerçek Foundry Local/Gemini hiç devreye girmez, sabit bir JSON yanıtı
    döndürür (bkz. tests/test_ui_routes.py::_DummyLLMProvider ile aynı desen)."""

    def __init__(self, response_json: dict):
        self._response = json.dumps(response_json)

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return self._response

    def is_available(self):
        return True


def _existing_candidate(**overrides) -> CandidateEvent:
    fields = dict(
        candidate_id="c1",
        source_type=SourceType.EMAIL,
        event_type=EventType.MEETING,
        title="Proje toplantısı",
        start_datetime=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        duration_minutes=60,
        status=CandidateStatus.ADDED_TO_CALENDAR,
    )
    fields.update(overrides)
    return CandidateEvent(**fields)


def _email(subject="Re: Proje toplantısı", body="Toplantı saati 16:00'a alındı.") -> UnifiedEmail:
    return UnifiedEmail(
        provider="gmail",
        account_id="acc1",
        message_id="msg-2",
        thread_id="thread-shared",
        subject=subject,
        sender="alerts@example.com",
        recipients=[],
        received_at=datetime.now(timezone.utc),
        body_text=body,
    )


def test_is_update_true_returns_whitelisted_changed_fields():
    llm = _ScriptedLLM({
        "is_update": True,
        "changed_fields": {"start_datetime": "2026-08-20T16:00:00", "unknown_field": "ignored"},
    })
    result = analyze_possible_update(llm, _existing_candidate(), _email())
    assert result["is_update"] is True
    assert result["changed_fields"] == {"start_datetime": "2026-08-20T16:00:00"}


def test_is_update_false_returns_empty_changed_fields():
    llm = _ScriptedLLM({"is_update": False, "changed_fields": {}})
    result = analyze_possible_update(llm, _existing_candidate(), _email(body="Teşekkürler, görüşürüz."))
    assert result["is_update"] is False
    assert result["changed_fields"] == {}


def test_is_update_true_but_no_recognized_fields_is_treated_as_not_update():
    llm = _ScriptedLLM({"is_update": True, "changed_fields": {"unknown_field": "x"}})
    result = analyze_possible_update(llm, _existing_candidate(), _email())
    assert result["is_update"] is False
    assert result["changed_fields"] == {}
