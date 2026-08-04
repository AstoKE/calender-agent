"""Hafta 1 dikey prototipi (bkz. docs/architecture-plan.md §20).

Elle girilen bir mesaj metnini candidate_event'e çevirir, SQLite'a yazar,
bir önizleme gösterir ve yalnızca kullanıcı onayı sonrası Google Calendar'a
yazar. RAG/Policy Store ve tam Conversation Layer bu prototipte YOK —
bunlar Hafta 2-3 kapsamı; burada tek bir kural (toplantı için varsayılan
60dk süre) doğrudan koda gömülü, RAG'dan retrieve edilmiyor.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from src.connectors.google_calendar import GoogleCalendarConnector
from src.core.models import CandidateEvent, CandidateStatus, EventType, SourceType
from src.providers.foundry_local import FoundryLocalProvider
from src.storage.db import get_connection, init_db

DEFAULT_TIMEZONE = "Europe/Istanbul"  # MVP basitleştirmesi; bkz. localization_preferences tablosu
DEFAULT_MEETING_DURATION_MINUTES = 60  # sabit kural örneği; Hafta 2'de RAG/Policy Store'dan gelecek


def _extraction_system_prompt() -> str:
    today = datetime.now().astimezone()
    return (
        "Sen bir takvim asistanısın. Kullanıcının mesajından bir etkinlik bilgisi çıkar.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Göreceli ifadeleri (yarın, önümüzdeki cuma, vb.) bu tarihe göre çöz.\n"
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme. Alanlar:\n"
        '{"event_type": "meeting|appointment|exam|deadline|travel|reservation|'
        'personal_commitment|other", '
        '"title": string veya null, '
        '"start_datetime": "YYYY-MM-DDTHH:MM:SS" veya null, '
        '"duration_minutes": integer veya null, '
        '"location": string veya null}'
    )


def extract_candidate_event(llm: FoundryLocalProvider, user_text: str) -> CandidateEvent:
    raw = llm.generate(_extraction_system_prompt(), user_text, json_output=True)
    fields = json.loads(raw)

    missing_fields = [
        name for name in ("title", "start_datetime", "duration_minutes") if not fields.get(name)
    ]

    return CandidateEvent(
        candidate_id=str(uuid.uuid4()),
        source_type=SourceType.CONVERSATION,
        source_references=[],
        source_languages=[],
        event_type=EventType(fields.get("event_type") or "other"),
        title=fields.get("title"),
        start_datetime=fields.get("start_datetime"),
        duration_minutes=fields.get("duration_minutes"),
        location=fields.get("location"),
        missing_fields=missing_fields,
        status=CandidateStatus.NEEDS_INFORMATION if missing_fields else CandidateStatus.READY_FOR_CONFIRMATION,
        extraction_reason="Kullanıcı mesajından doğrudan çıkarıldı (konuşma akışı).",
    )


def fill_missing_fields_interactively(candidate: CandidateEvent) -> None:
    """Minimal, tek adımlı soru döngüsü. Tam Conversation Layer Hafta 2 kapsamı."""
    if "title" in candidate.missing_fields:
        candidate.title = input("Etkinliğin başlığı ne olsun? ").strip()

    if "duration_minutes" in candidate.missing_fields:
        if candidate.event_type == EventType.MEETING.value or candidate.event_type == EventType.MEETING:
            candidate.duration_minutes = DEFAULT_MEETING_DURATION_MINUTES
            print(f"(Kural: toplantılar için varsayılan süre {DEFAULT_MEETING_DURATION_MINUTES} dakika uygulandı.)")
        else:
            raw = input("Süre kaç dakika? ").strip()
            candidate.duration_minutes = int(raw) if raw else None

    if "start_datetime" in candidate.missing_fields:
        raw = input("Tarih/saat (YYYY-MM-DDTHH:MM:SS)? ").strip()
        candidate.start_datetime = raw or None

    candidate.missing_fields = [
        name
        for name in ("title", "start_datetime", "duration_minutes")
        if not getattr(candidate, name)
    ]
    candidate.status = (
        CandidateStatus.NEEDS_INFORMATION if candidate.missing_fields else CandidateStatus.READY_FOR_CONFIRMATION
    )


def save_candidate(conn, candidate: CandidateEvent) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO candidate_events (
            candidate_id, source_type, source_references, source_languages,
            event_type, title, start_datetime, end_datetime, timezone,
            duration_minutes, location, online_meeting_url, participants,
            description, importance, reminders, preparation_time_minutes,
            travel_time_minutes, recurrence, missing_fields, ambiguous_fields,
            confidence, status, extraction_reason, retrieved_policy_ids,
            retrieved_correction_ids, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate.candidate_id,
            candidate.source_type if isinstance(candidate.source_type, str) else candidate.source_type.value,
            json.dumps(candidate.source_references),
            json.dumps(candidate.source_languages),
            candidate.event_type if isinstance(candidate.event_type, str) else candidate.event_type.value,
            candidate.title,
            candidate.start_datetime.isoformat() if candidate.start_datetime else None,
            candidate.end_datetime.isoformat() if candidate.end_datetime else None,
            DEFAULT_TIMEZONE,
            candidate.duration_minutes,
            candidate.location,
            candidate.online_meeting_url,
            json.dumps([p.model_dump() for p in candidate.participants]),
            candidate.description,
            candidate.importance,
            json.dumps([r.model_dump() for r in candidate.reminders]),
            candidate.preparation_time_minutes,
            candidate.travel_time_minutes,
            candidate.recurrence,
            json.dumps(candidate.missing_fields),
            json.dumps(candidate.ambiguous_fields),
            candidate.confidence,
            candidate.status if isinstance(candidate.status, str) else candidate.status.value,
            candidate.extraction_reason,
            json.dumps(candidate.retrieved_policy_ids),
            json.dumps(candidate.retrieved_correction_ids),
            now,
            now,
        ),
    )


def update_candidate_status(conn, candidate_id: str, status: CandidateStatus) -> None:
    conn.execute(
        "UPDATE candidate_events SET status = ?, updated_at = ? WHERE candidate_id = ?",
        (status.value, datetime.now(timezone.utc).isoformat(), candidate_id),
    )


def record_audit(conn, action: str, entity_id: str, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (id, actor, action, entity_type, entity_id, reason, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            "user",
            action,
            "candidate_event",
            entity_id,
            reason,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def print_preview(candidate: CandidateEvent) -> None:
    start = candidate.start_datetime
    end = None
    if start and candidate.duration_minutes:
        start_dt = start if isinstance(start, datetime) else datetime.fromisoformat(start)
        end = start_dt + timedelta(minutes=candidate.duration_minutes)

    print("\n--- Önizleme ---")
    print(f"Başlık:   {candidate.title}")
    print(f"Zaman:    {start} -> {end}")
    print(f"Konum:    {candidate.location or '(belirtilmedi)'}")
    print(f"Tür:      {candidate.event_type}")
    print("----------------\n")


def main() -> None:
    init_db()
    llm = FoundryLocalProvider(model_alias="qwen3-4b")

    user_text = input("Ne planlamak istiyorsunuz? ").strip()
    candidate = extract_candidate_event(llm, user_text)

    if candidate.missing_fields:
        fill_missing_fields_interactively(candidate)

    with get_connection() as conn:
        save_candidate(conn, candidate)

    print_preview(candidate)
    approval = input("Onaylıyor musunuz? [e/h] ").strip().lower()

    with get_connection() as conn:
        if approval != "e":
            update_candidate_status(conn, candidate.candidate_id, CandidateStatus.REJECTED)
            record_audit(conn, "reject", candidate.candidate_id, "Kullanıcı reddetti.")
            print("Reddedildi, takvime yazılmadı.")
            return

        start_dt = candidate.start_datetime
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt)
        end_dt = start_dt + timedelta(minutes=candidate.duration_minutes or DEFAULT_MEETING_DURATION_MINUTES)

        calendar = GoogleCalendarConnector(account_id="astokrappersteam")
        event_id = calendar.create_event(
            {
                "summary": candidate.title,
                "location": candidate.location,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": DEFAULT_TIMEZONE},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            }
        )

        update_candidate_status(conn, candidate.candidate_id, CandidateStatus.ADDED_TO_CALENDAR)
        record_audit(conn, "approve_and_write", candidate.candidate_id, f"Google Calendar event_id={event_id}")

    print(f"Takvime eklendi. event_id={event_id}")


if __name__ == "__main__":
    main()
