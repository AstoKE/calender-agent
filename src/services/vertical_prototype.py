"""Hafta 1 dikey prototipi (bkz. docs/architecture-plan.md §20).

Elle girilen bir mesaj metnini candidate_event'e çevirir, SQLite'a yazar,
bir önizleme gösterir ve yalnızca kullanıcı onayı sonrası Google Calendar'a
yazar. Basit bir niyet tespiti (src/services/intent.py) mesajı create_event/
query_calendar/update_event/other olarak yönlendirir. RAG/Policy Store ve
tam Conversation Layer (çok turlu diyalog durumu) bu prototipte hâlâ YOK —
bunlar Hafta 2-3'ün geri kalanı; burada tek bir kural (toplantı için
varsayılan 60dk süre) doğrudan koda gömülü, RAG'dan retrieve edilmiyor.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from src.connectors.google_calendar import GoogleCalendarConnector
from src.core.models import CandidateEvent, CandidateStatus, EventType, IntentType, SourceType
from src.providers.foundry_local import FoundryLocalProvider
from src.services.availability import find_conflicts, suggest_alternative_slots
from src.services.intent import classify_intent
from src.services.timeutil import (
    DEFAULT_TIMEZONE,
    ensure_timezone,
    format_date_tr,
    parse_clock_time,
    parse_duration_minutes,
)
from src.storage.db import get_connection, init_db

DEFAULT_MEETING_DURATION_MINUTES = 60  # sabit kural örneği; Hafta 2'de RAG/Policy Store'dan gelecek


def _extraction_system_prompt() -> str:
    today = datetime.now().astimezone()
    return (
        "Sen bir takvim asistanısın. Kullanıcının mesajından bir etkinlik bilgisi çıkar.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Göreceli ifadeleri (yarın, önümüzdeki cuma, vb.) bu tarihe göre çöz.\n"
        "KRİTİK KURALLAR:\n"
        "1. Mesajda AÇIKÇA belirtilmeyen hiçbir bilgiyi UYDURMA. Konum, kişi gibi "
        "alanlar mesajda geçmiyorsa null bırak — tahmin etme.\n"
        "2. Saat belirsizse (örn: 'saat 1', 'saat 3' — sabah mı öğleden sonra mı "
        "belirtilmemiş ve bağlamdan da açıkça çıkarılamıyor): tarihi yine de doğru "
        "hesapla ve start_datetime'a yaz (saat kısmı için en olası tahmini kullan, "
        "tarihi KAYBETME), AYRICA ambiguous_fields listesine \"start_datetime\" ekle "
        "— kullanıcıya saati tekrar soracağız. Sadece net bağlamdan (örn. 'akşam "
        "saat 8', 'sabah 9') çıkarım yaparsan ambiguous_fields'a ekleme.\n"
        "3. Emin olmadığın her alan için tahmin yerine null + ambiguous_fields tercih et.\n"
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme. Alanlar:\n"
        '{"event_type": "meeting|appointment|exam|deadline|travel|reservation|'
        'personal_commitment|other", '
        '"title": string veya null, '
        '"start_datetime": "YYYY-MM-DDTHH:MM:SS" veya null, '
        '"duration_minutes": integer veya null, '
        '"location": string veya null, '
        '"ambiguous_fields": [string]}'
    )


def extract_candidate_event(llm: FoundryLocalProvider, user_text: str) -> CandidateEvent:
    raw = llm.generate(_extraction_system_prompt(), user_text, json_output=True)
    fields = json.loads(raw)

    ambiguous_fields = fields.get("ambiguous_fields") or []
    missing_fields = [
        name
        for name in ("title", "start_datetime", "duration_minutes")
        if not fields.get(name) and name not in ambiguous_fields
    ]

    candidate = CandidateEvent(
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
        ambiguous_fields=ambiguous_fields,
        status=(
            CandidateStatus.NEEDS_INFORMATION
            if (missing_fields or ambiguous_fields)
            else CandidateStatus.READY_FOR_CONFIRMATION
        ),
        extraction_reason="Kullanıcı mesajından doğrudan çıkarıldı (konuşma akışı).",
    )
    candidate.start_datetime = ensure_timezone(candidate.start_datetime)
    return candidate


MAX_CLARIFICATION_ATTEMPTS = 3


def fill_missing_fields_interactively(candidate: CandidateEvent) -> None:
    """Minimal, tek adımlı soru döngüsü. Tam Conversation Layer Hafta 2 kapsamı."""
    if "title" in candidate.missing_fields:
        candidate.title = input("Etkinliğin başlığı ne olsun? ").strip()

    if "duration_minutes" in candidate.missing_fields:
        if candidate.event_type == EventType.MEETING.value or candidate.event_type == EventType.MEETING:
            candidate.duration_minutes = DEFAULT_MEETING_DURATION_MINUTES
            print(f"(Kural: toplantılar için varsayılan süre {DEFAULT_MEETING_DURATION_MINUTES} dakika uygulandı.)")
        else:
            for _ in range(MAX_CLARIFICATION_ATTEMPTS):
                raw = input("Süre ne kadar? (örn: 30, 1 saat) ").strip()
                minutes = parse_duration_minutes(raw)
                if minutes:
                    candidate.duration_minutes = minutes
                    break
                print("Anlayamadım, bir sayı içeren şekilde tekrar dener misiniz? (örn: 45 veya '1 saat')")

    if "start_datetime" in candidate.missing_fields:
        for _ in range(MAX_CLARIFICATION_ATTEMPTS):
            raw = input("Tarih/saat (YYYY-MM-DDTHH:MM:SS)? ").strip()
            try:
                candidate.start_datetime = ensure_timezone(raw or None)
                break
            except Exception:
                print("Bu formatı anlayamadım, YYYY-MM-DDTHH:MM:SS biçiminde tekrar dener misiniz?")

    if "start_datetime" in candidate.ambiguous_fields:
        for _ in range(MAX_CLARIFICATION_ATTEMPTS):
            raw = input("Saat belirsiz görünüyor — tam olarak kaçta? (örn: 13:00) ").strip()
            clock = parse_clock_time(raw) if raw else None
            if not clock:
                print("Saati anlayamadım, tekrar dener misiniz? (örn: 13:00, 13.30, 'saat 9')")
                continue
            if isinstance(candidate.start_datetime, datetime):
                date_part = candidate.start_datetime.date().isoformat()
            else:
                date_part = (candidate.start_datetime or datetime.now().date().isoformat())[:10]
            try:
                candidate.start_datetime = ensure_timezone(f"{date_part}T{clock}:00")
                break
            except Exception:
                print("Bir sorun oldu, tekrar dener misiniz?")

    candidate.missing_fields = [
        name
        for name in ("title", "start_datetime", "duration_minutes")
        if not getattr(candidate, name)
    ]
    candidate.ambiguous_fields = [
        name for name in candidate.ambiguous_fields if not getattr(candidate, name, None)
    ]
    candidate.status = (
        CandidateStatus.NEEDS_INFORMATION
        if (candidate.missing_fields or candidate.ambiguous_fields)
        else CandidateStatus.READY_FOR_CONFIRMATION
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


def print_preview(candidate: CandidateEvent, conflict_note: str = "Yok") -> None:
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
    print(f"Çakışma:  {conflict_note}")
    print("----------------\n")


def resolve_conflicts_interactively(calendar: GoogleCalendarConnector, candidate: CandidateEvent) -> str:
    """Çakışma varsa alternatif saatler önerir, kullanıcı seçimine göre
    candidate.start_datetime'ı günceller. Dönüş: preview'da gösterilecek not."""
    start_dt = candidate.start_datetime
    if isinstance(start_dt, str):
        start_dt = datetime.fromisoformat(start_dt)
    end_dt = start_dt + timedelta(minutes=candidate.duration_minutes)

    conflicts = find_conflicts(calendar, start_dt, end_dt)
    if not conflicts:
        return "Yok"

    print(f"\n⚠ Çakışma bulundu: {start_dt:%H:%M}-{end_dt:%H:%M} aralığında zaten bir etkinliğiniz var.")
    alternatives = suggest_alternative_slots(calendar, candidate.duration_minutes, end_dt)

    if not alternatives:
        print("Yakın zamanda uygun bir alternatif bulamadım.")
        choice = input("Yine de bu saatte devam edelim mi? [e/h] ").strip().lower()
        return "Var (kullanıcı yine de onayladı)" if choice == "e" else "Var (çözülmedi)"

    print("Alternatif uygun saatler:")
    for i, alt in enumerate(alternatives, 1):
        print(f"  {i}. {format_date_tr(alt)}, {alt:%H:%M}")
    choice = input(
        f"Bir alternatif seçin (1-{len(alternatives)}), yine de bu saatte devam edin (d), veya iptal edin (i): "
    ).strip().lower()

    if choice.isdigit() and 1 <= int(choice) <= len(alternatives):
        chosen = alternatives[int(choice) - 1]
        candidate.start_datetime = chosen
        return f"Vardı, {chosen:%d.%m %H:%M}'e taşındı"
    if choice == "d":
        return "Var (kullanıcı yine de onayladı)"
    return "Var (iptal edilecek)"


def handle_query_calendar(account_id: str, range_start: datetime | None, range_end: datetime | None) -> None:
    if range_start is None or range_end is None:
        print("Hangi tarih aralığını merak ediyorsunuz, tam olarak söyler misiniz?")
        return

    calendar = GoogleCalendarConnector(account_id=account_id)
    events = calendar.list_events(range_start, range_end)

    if not events:
        print(
            f"{format_date_tr(range_start)}, {range_start:%H:%M} - "
            f"{format_date_tr(range_end)}, {range_end:%H:%M} arasında hiç etkinliğiniz yok."
        )
        return

    print(f"\n{format_date_tr(range_start)} tarihinde {len(events)} etkinliğiniz var:")
    for e in events:
        start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
        print(f"  - {e.get('summary', '(başlıksız)')} | {start}")
    print()


def main() -> None:
    init_db()
    llm = FoundryLocalProvider(model_alias="qwen3-4b")
    account_id = "astokrappersteam"

    user_text = input("Ne planlamak istiyorsunuz? ").strip()
    intent = classify_intent(llm, user_text)

    if intent.intent == IntentType.QUERY_CALENDAR:
        handle_query_calendar(account_id, intent.query_range_start, intent.query_range_end)
        return
    if intent.intent == IntentType.UPDATE_EVENT:
        print("Var olan etkinlikleri güncellemeyi henüz desteklemiyorum, bu yakında eklenecek.")
        return
    if intent.intent == IntentType.OTHER:
        print("Bunu tam anlayamadım. Şu an yeni etkinlik eklemek ve takviminizi sormak için kullanılabilirim.")
        return

    candidate = extract_candidate_event(llm, user_text)

    if candidate.missing_fields or candidate.ambiguous_fields:
        fill_missing_fields_interactively(candidate)

    if candidate.missing_fields or candidate.ambiguous_fields:
        print("Gerekli bilgileri tamamlayamadım, baştan denemek ister misiniz?")
        return

    calendar = GoogleCalendarConnector(account_id=account_id)
    conflict_note = "Yok"
    if candidate.start_datetime and candidate.duration_minutes:
        conflict_note = resolve_conflicts_interactively(calendar, candidate)

    with get_connection() as conn:
        save_candidate(conn, candidate)

    if conflict_note == "Var (iptal edilecek)":
        with get_connection() as conn:
            update_candidate_status(conn, candidate.candidate_id, CandidateStatus.REJECTED)
            record_audit(conn, "reject", candidate.candidate_id, "Çözülmeyen çakışma nedeniyle iptal.")
        print("İptal edildi, takvime yazılmadı.")
        return

    print_preview(candidate, conflict_note)
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
