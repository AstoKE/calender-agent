from src.core.models import CandidateStatus, EventType, SourceType
from src.services.extraction import build_candidate_from_fields


def _base_fields(**overrides):
    fields = {
        "event_type": "meeting",
        "title": "Proje toplantısı",
        "start_datetime": "2026-08-19T14:00:00+03:00",
        "duration_minutes": 60,
        "location": None,
        "ambiguous_fields": [],
    }
    fields.update(overrides)
    return fields


def _build(fields):
    return build_candidate_from_fields(
        fields,
        source_type=SourceType.CONVERSATION,
        source_references=[],
        source_languages=[],
        extraction_reason="test",
    )


def test_full_fields_ready_for_confirmation():
    candidate = _build(_base_fields())
    assert candidate.status == CandidateStatus.READY_FOR_CONFIRMATION
    assert candidate.missing_fields == []
    assert candidate.ambiguous_fields == []
    assert candidate.event_type == EventType.MEETING


def test_missing_title_marks_needs_information():
    candidate = _build(_base_fields(title=None))
    assert "title" in candidate.missing_fields
    assert candidate.status == CandidateStatus.NEEDS_INFORMATION


def test_missing_duration_marks_needs_information():
    candidate = _build(_base_fields(duration_minutes=None))
    assert "duration_minutes" in candidate.missing_fields
    assert candidate.status == CandidateStatus.NEEDS_INFORMATION


def test_ambiguous_fields_whitelist_filters_unknown_field_name():
    # Regresyon: model ambiguous_fields'e fill_missing_fields_interactively'nin
    # sormayı bilmediği bir alan adı (örn. "location") koyarsa, bu candidate'ı
    # sonsuza kadar kilitlememeli — canlı testte tam olarak bu oldu (kullanıcı
    # sorulan tüm alanları doğru cevaplasa bile candidate hiç onaya gelmiyordu).
    candidate = _build(_base_fields(ambiguous_fields=["location", "start_datetime"]))
    assert candidate.ambiguous_fields == ["start_datetime"]
    assert "location" not in candidate.ambiguous_fields


def test_ambiguous_start_datetime_does_not_count_as_missing():
    fields = _base_fields(start_datetime="2026-08-19T14:00:00+03:00", ambiguous_fields=["start_datetime"])
    candidate = _build(fields)
    assert "start_datetime" not in candidate.missing_fields
    assert candidate.ambiguous_fields == ["start_datetime"]
    assert candidate.status == CandidateStatus.NEEDS_INFORMATION


def test_invalid_event_type_falls_back_to_other():
    # Regresyon: model bazen enum seçenek listesinin tamamını olduğu gibi
    # döndürüyor (örn. "meeting|appointment|exam|...") — ValueError yerine
    # güvenli varsayılana düşülmeli.
    candidate = _build(_base_fields(event_type="meeting|appointment|exam|deadline"))
    assert candidate.event_type == EventType.OTHER


def test_null_event_type_falls_back_to_other():
    candidate = _build(_base_fields(event_type=None))
    assert candidate.event_type == EventType.OTHER


def test_no_dates_uydurma_stays_null_without_ensure_timezone_crash():
    candidate = _build(_base_fields(start_datetime=None, ambiguous_fields=["start_datetime"]))
    assert candidate.start_datetime is None
    assert "start_datetime" in candidate.ambiguous_fields
