"""Candidate Event Queue — persistence (bkz. docs/architecture-plan.md §12).

`policies/store.py` ile aynı stil: her fonksiyon kendi `get_connection()`'ını
açar (self-contained). `vertical_prototype.py`'deki `save_candidate`/
`update_candidate_status`/`record_audit`'ten KASITLI olarak ayrı — onlar
`conn` paylaşarak tek bir transaction'da birden fazla yazım yapıyor (CLI'nın
interaktif akışı için), burada ise her çağrı kendi başına (web route'ları
tek seferlik, basit istekler)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.core.models import CandidateEvent, CandidateStatus
from src.memory.correction_memory import candidate_snapshot
from src.services.extraction import required_fields_for
from src.services.timeutil import DEFAULT_TIMEZONE, ensure_timezone
from src.storage.db import get_connection


def save_new_candidate(candidate: CandidateEvent, source_email_row_id: str) -> None:
    """Yeni bir candidate'ı kuyruğa (candidate_events) ekler ve mail kaynağını
    (candidate_sources, relation_type='origin') bağlar. Hiçbir onay/inceleme
    TETİKLEMEZ — mail taraması artık candidate'ı burada bırakıp geçiyor,
    onay web UI'dan geliyor (bkz. scan_inbox.py)."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
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
        conn.execute(
            "INSERT INTO candidate_sources (candidate_id, email_message_id, relation_type, created_at) "
            "VALUES (?,?,?,?)",
            (candidate.candidate_id, source_email_row_id, "origin", now),
        )


def row_to_candidate(row) -> CandidateEvent:
    return CandidateEvent(
        candidate_id=row["candidate_id"],
        source_type=row["source_type"],
        source_references=json.loads(row["source_references"] or "[]"),
        source_languages=json.loads(row["source_languages"] or "[]"),
        event_type=row["event_type"],
        title=row["title"],
        start_datetime=row["start_datetime"],
        end_datetime=row["end_datetime"],
        timezone=row["timezone"],
        duration_minutes=row["duration_minutes"],
        location=row["location"],
        online_meeting_url=row["online_meeting_url"],
        participants=json.loads(row["participants"] or "[]"),
        description=row["description"],
        importance=row["importance"],
        reminders=json.loads(row["reminders"] or "[]"),
        preparation_time_minutes=row["preparation_time_minutes"],
        travel_time_minutes=row["travel_time_minutes"],
        recurrence=row["recurrence"],
        missing_fields=json.loads(row["missing_fields"] or "[]"),
        ambiguous_fields=json.loads(row["ambiguous_fields"] or "[]"),
        confidence=row["confidence"] or 0.0,
        status=row["status"],
        extraction_reason=row["extraction_reason"],
        retrieved_policy_ids=json.loads(row["retrieved_policy_ids"] or "[]"),
        retrieved_correction_ids=json.loads(row["retrieved_correction_ids"] or "[]"),
    )


_PENDING_QUERY = """
    SELECT c.*, em.account_id AS source_account_id, em.sender AS source_sender,
           em.subject AS source_subject
    FROM candidate_events c
    JOIN candidate_sources cs ON cs.candidate_id = c.candidate_id AND cs.relation_type = 'origin'
    JOIN email_messages em ON em.id = cs.email_message_id
    WHERE c.status IN ('NEEDS_INFORMATION', 'READY_FOR_CONFIRMATION', 'UPDATE_SUGGESTED')
"""


def list_pending_candidates(account_id: str | None = None) -> list[dict]:
    """Web'de "Gelen Öneriler" olarak gösterilecek candidate'ları döner —
    yalnızca MAIL kaynaklı (source_type='email'): konuşma kaynaklı
    candidate'ların hiçbir account_id bağlantısı yok (CLI'da anlık bağlanıyor,
    DB'ye hiç yazılmıyor), bu yüzden hangi takvime yazılacağı bilinemez.

    ``account_id`` verilirse yalnızca o hesaba ait öneriler döner (Ana
    Sayfa'nın aktif-hesap filtresi için) — ``None`` (varsayılan) eski
    davranışı korur, tüm hesapların önerilerini döner (Gelen Öneriler
    ekranı hâlâ tüm hesapları birlikte gösteriyor)."""
    query = _PENDING_QUERY
    params: tuple = ()
    if account_id is not None:
        query += " AND em.account_id = ?"
        params = (account_id,)
    with get_connection() as conn:
        rows = conn.execute(query + " ORDER BY c.created_at DESC", params).fetchall()
    return [_row_to_pending_dict(r) for r in rows]


def count_pending_candidates(account_id: str | None = None) -> int:
    """Nav rozeti/Ana Sayfa istatistik satırı için — tüm Pydantic modellerini
    hidratlamadan yalnızca sayıyı döner."""
    query = "SELECT COUNT(*) FROM candidate_events c JOIN candidate_sources cs " \
        "ON cs.candidate_id = c.candidate_id AND cs.relation_type = 'origin' " \
        "JOIN email_messages em ON em.id = cs.email_message_id " \
        "WHERE c.status IN ('NEEDS_INFORMATION', 'READY_FOR_CONFIRMATION', 'UPDATE_SUGGESTED')"
    params: tuple = ()
    if account_id is not None:
        query += " AND em.account_id = ?"
        params = (account_id,)
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()[0]


def get_pending_candidate(candidate_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(_PENDING_QUERY + " AND c.candidate_id = ?", (candidate_id,)).fetchone()
    return _row_to_pending_dict(row) if row else None


def _row_to_pending_dict(row) -> dict:
    return {
        "candidate": row_to_candidate(row),
        "account_id": row["source_account_id"],
        "sender": row["source_sender"],
        "subject": row["source_subject"],
        "google_event_id": row["google_event_id"] if "google_event_id" in row.keys() else None,
        "previous_snapshot": (
            json.loads(row["previous_snapshot"])
            if "previous_snapshot" in row.keys() and row["previous_snapshot"]
            else None
        ),
    }


def update_candidate_fields(candidate_id: str, **fields) -> None:
    """Düzenleme formundan gelen alanları uygular (title/start_datetime/
    duration_minutes/importance/location), sonra missing_fields/ambiguous_fields/
    status'u aynı whitelist mantığıyla (bkz. extraction.py CLARIFIABLE_FIELDS)
    yeniden hesaplar — kullanıcı eksik bir alanı doldurunca candidate otomatik
    olarak READY_FOR_CONFIRMATION'a geçsin diye."""
    pending = get_pending_candidate(candidate_id)
    if pending is None:
        raise ValueError(f"Candidate bulunamadı: {candidate_id}")
    candidate = pending["candidate"]

    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key == "start_datetime":
            value = ensure_timezone(value)
        elif key == "duration_minutes":
            value = int(value)
        setattr(candidate, key, value)

    # "f in CLARIFIABLE_FIELDS" tek başına yanlıştı — bu statik bir isim
    # listesi (bkz. extraction.py), alanın HÂLÂ değersiz olup olmadığını
    # değil. Kullanıcı formda değeri doldursa bile ambiguous_fields'ta
    # kalıyordu, "Eksik/belirsiz alanlar" uyarısı hiç kapanmıyordu (canlı
    # testte görüldü). vertical_prototype.py'deki doğru desenle aynı hizaya
    # getirildi: alan yalnızca hâlâ değersizse listede kalır.
    # required_fields_for: DEADLINE tipi etkinliklerde duration_minutes hiç
    # gerekmiyor (bkz. extraction.py) — CLARIFIABLE_FIELDS sabit listesini
    # doğrudan kullanmak bu istisnayı burada da tekrar kaybederdi.
    required = required_fields_for(candidate.event_type)
    candidate.ambiguous_fields = [
        f for f in candidate.ambiguous_fields if f in required and not getattr(candidate, f, None)
    ]
    candidate.missing_fields = [
        name
        for name in required
        if not getattr(candidate, name) and name not in candidate.ambiguous_fields
    ]
    candidate.status = (
        CandidateStatus.NEEDS_INFORMATION
        if (candidate.missing_fields or candidate.ambiguous_fields)
        else CandidateStatus.READY_FOR_CONFIRMATION
    )

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE candidate_events SET
                title = ?, start_datetime = ?, duration_minutes = ?, importance = ?,
                location = ?, missing_fields = ?, ambiguous_fields = ?, status = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            (
                candidate.title,
                candidate.start_datetime.isoformat() if candidate.start_datetime else None,
                candidate.duration_minutes,
                candidate.importance,
                candidate.location,
                json.dumps(candidate.missing_fields),
                json.dumps(candidate.ambiguous_fields),
                candidate.status if isinstance(candidate.status, str) else candidate.status.value,
                now,
                candidate_id,
            ),
        )


def update_candidate_status(candidate_id: str, status: CandidateStatus) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE candidate_events SET status = ?, updated_at = ? WHERE candidate_id = ?",
            (status.value, datetime.now(timezone.utc).isoformat(), candidate_id),
        )


def set_candidate_google_event_id(candidate_id: str, event_id: str) -> None:
    """`calendar.create_event()`/`update_event()` başarılı olduktan hemen sonra
    çağrılır — bu id daha önce hiçbir yerde saklanmıyordu, bu yüzden mail
    kaynaklı bir güncelleme önerisi bile onaylansa `update_event`'e hangi
    Google etkinliğinin hedefleneceği bilinemiyordu."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE candidate_events SET google_event_id = ?, updated_at = ? WHERE candidate_id = ?",
            (event_id, datetime.now(timezone.utc).isoformat(), candidate_id),
        )


def find_related_candidate_by_thread(thread_id: str, exclude_email_row_id: str) -> dict | None:
    """Aynı Gmail thread'inde daha önce işlenmiş (bu yeni mailin kendisi HARİÇ)
    bir candidate var mı? REJECTED/DISMISSED hariç tutulur — kullanıcı bir
    öneriyi açıkça reddettiyse, aynı thread'deki sonraki bir mail onu sessizce
    diriltmemeli. Birden fazla eşleşme varsa en son güncelleneni döner (bkz.
    docs/architecture-plan.md §9 "aynı thread_id -> kesin sinyal")."""
    if not thread_id:
        return None
    query = (
        "SELECT c.*, em.account_id AS source_account_id, em.sender AS source_sender, "
        "em.subject AS source_subject "
        "FROM candidate_events c "
        "JOIN candidate_sources cs ON cs.candidate_id = c.candidate_id "
        "JOIN email_messages em ON em.id = cs.email_message_id "
        "WHERE em.thread_id = ? AND em.id != ? AND c.status NOT IN ('REJECTED', 'DISMISSED') "
        "ORDER BY c.updated_at DESC LIMIT 1"
    )
    with get_connection() as conn:
        row = conn.execute(query, (thread_id, exclude_email_row_id)).fetchone()
    return _row_to_pending_dict(row) if row else None


def apply_update_suggestion(candidate_id: str, changed_fields: dict, source_email_row_id: str) -> None:
    """Mevcut candidate'ın alanlarının ÜZERİNE önerilen değişikliği yazar,
    ama önce eski hâlin anlık görüntüsünü `previous_snapshot`'a kaydeder
    (Öneriler ekranında önce/sonra göstermek + reddedilirse geri almak için,
    bkz. `revert_update_suggestion`). `candidate_snapshot` zaten Düzeltmelerim
    ekranının diff'i için var olan aynı alan kümesini kullanıyor (reuse)."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM candidate_events WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise ValueError(f"Candidate bulunamadı: {candidate_id}")
    candidate = row_to_candidate(row)
    previous = candidate_snapshot(candidate)

    for key, value in changed_fields.items():
        if key == "start_datetime" and value:
            value = ensure_timezone(value)
        elif key == "duration_minutes" and value is not None:
            value = int(value)
        setattr(candidate, key, value)

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE candidate_events SET
                title = ?, start_datetime = ?, duration_minutes = ?, importance = ?,
                location = ?, previous_snapshot = ?, status = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            (
                candidate.title,
                candidate.start_datetime.isoformat() if candidate.start_datetime else None,
                candidate.duration_minutes,
                candidate.importance,
                candidate.location,
                json.dumps(previous),
                CandidateStatus.UPDATE_SUGGESTED.value,
                now,
                candidate_id,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO candidate_sources (candidate_id, email_message_id, relation_type, created_at) "
            "VALUES (?,?,?,?)",
            (candidate_id, source_email_row_id, "update", now),
        )


def revert_update_suggestion(candidate_id: str) -> None:
    """Bir güncelleme önerisi reddedildiğinde çağrılır: `previous_snapshot`'taki
    alanları geri yükler, durumu `ADDED_TO_CALENDAR`'a döndürür (gerçek
    takvim etkinliği hiç değişmedi, yalnızca öneri iptal edildi — bu yüzden
    `REJECTED` DEĞİL, o "bunu hiç takvime ekleme" anlamına gelir)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT previous_snapshot FROM candidate_events WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
    if row is None or not row["previous_snapshot"]:
        return
    previous = json.loads(row["previous_snapshot"])
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE candidate_events SET
                title = ?, start_datetime = ?, duration_minutes = ?, importance = ?,
                location = ?, previous_snapshot = NULL, status = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            (
                previous.get("title"),
                previous.get("start_datetime"),
                previous.get("duration_minutes"),
                previous.get("importance"),
                previous.get("location"),
                CandidateStatus.ADDED_TO_CALENDAR.value,
                now,
                candidate_id,
            ),
        )


def record_candidate_audit(action: str, candidate_id: str, reason: str) -> None:
    with get_connection() as conn:
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
                candidate_id,
                reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
