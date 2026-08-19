"""Ortak candidate-event alan eşleme mantığı.

src/services/vertical_prototype.py (konuşma) ve src/services/mail_analysis.py
(e-posta) aynı LLM-çıktısı-şeklini kullanır; bu modül o JSON'u CandidateEvent'e
çevirme mantığını tekrar etmemek için tek yerde tutar.
"""

from __future__ import annotations

import uuid

from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.services.timeutil import ensure_timezone


def _coerce_event_type(raw_value) -> EventType:
    """Model, alan adının kendisini seçmek yerine bazen tüm enum seçenek
    listesini ("meeting|appointment|...") olduğu gibi döndürüyor (canlı
    testte görüldü) — bu durumda ValueError yerine güvenli varsayılana düş."""
    try:
        return EventType(raw_value) if raw_value else EventType.OTHER
    except ValueError:
        return EventType.OTHER


# fill_missing_fields_interactively (vertical_prototype.py) yalnızca bu üç
# alan için nasıl soru soracağını biliyor. Model ambiguous_fields'e başka bir
# alan adı (örn. "location", "duration_minutes" hem missing hem ambiguous)
# koyarsa, o alan hiçbir zaman sorulmuyor ve candidate sonsuza kadar
# "belirsiz" damgalı kalıp sessizce atlanıyordu (canlı testte görüldü —
# kullanıcı sorulan tüm alanları doğru cevaplasa bile). Modelin
# ambiguous_fields çıktısını bu üçle sınırlamak, arayüzün gerçekten
# çözebileceği alanlar dışında hiçbir şeyin candidate'ı kilitlememesini
# garanti eder.
CLARIFIABLE_FIELDS = ("title", "start_datetime", "duration_minutes")


def build_candidate_from_fields(
    fields: dict,
    source_type: SourceType,
    source_references: list[str],
    source_languages: list[str],
    extraction_reason: str,
) -> CandidateEvent:
    ambiguous_fields = [f for f in (fields.get("ambiguous_fields") or []) if f in CLARIFIABLE_FIELDS]
    missing_fields = [
        name
        for name in CLARIFIABLE_FIELDS
        if not fields.get(name) and name not in ambiguous_fields
    ]

    candidate = CandidateEvent(
        candidate_id=str(uuid.uuid4()),
        source_type=source_type,
        source_references=source_references,
        source_languages=source_languages,
        event_type=_coerce_event_type(fields.get("event_type")),
        title=fields.get("title"),
        start_datetime=fields.get("start_datetime"),
        duration_minutes=fields.get("duration_minutes"),
        location=fields.get("location"),
        online_meeting_url=fields.get("online_meeting_url"),
        missing_fields=missing_fields,
        ambiguous_fields=ambiguous_fields,
        status=(
            CandidateStatus.NEEDS_INFORMATION
            if (missing_fields or ambiguous_fields)
            else CandidateStatus.READY_FOR_CONFIRMATION
        ),
        extraction_reason=extraction_reason,
    )
    candidate.start_datetime = ensure_timezone(candidate.start_datetime)
    return candidate
