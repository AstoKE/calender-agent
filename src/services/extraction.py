"""Ortak candidate-event alan eşleme mantığı.

src/services/vertical_prototype.py (konuşma) ve src/services/mail_analysis.py
(e-posta) aynı LLM-çıktısı-şeklini kullanır; bu modül o JSON'u CandidateEvent'e
çevirme mantığını tekrar etmemek için tek yerde tutar.
"""

from __future__ import annotations

import uuid

from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.services.timeutil import ensure_timezone


def build_candidate_from_fields(
    fields: dict,
    source_type: SourceType,
    source_references: list[str],
    source_languages: list[str],
    extraction_reason: str,
) -> CandidateEvent:
    ambiguous_fields = fields.get("ambiguous_fields") or []
    missing_fields = [
        name
        for name in ("title", "start_datetime", "duration_minutes")
        if not fields.get(name) and name not in ambiguous_fields
    ]

    candidate = CandidateEvent(
        candidate_id=str(uuid.uuid4()),
        source_type=source_type,
        source_references=source_references,
        source_languages=source_languages,
        event_type=EventType(fields.get("event_type") or "other"),
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
