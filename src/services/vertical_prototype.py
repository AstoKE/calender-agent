"""Hafta 1-2 dikey prototipi (bkz. docs/architecture-plan.md §20).

Elle girilen bir mesaj metnini candidate_event'e çevirir, SQLite'a yazar,
bir önizleme gösterir ve yalnızca kullanıcı onayı sonrası Google Calendar'a
yazar. Niyet tespiti (src/services/intent.py) mesajı create_event/
query_calendar/update_event/define_policy/other olarak yönlendirir.
Policy Store + RAG retrieval (src/policies, src/rag) artık bağlı: eksik
süre önce kullanıcının tanımladığı kurallardan retrieve edilir, bulunamazsa
DEFAULT_MEETING_DURATION_MINUTES'a (sistem varsayılanı, en düşük öncelik —
bkz. §9 politika önceliği) düşer. `main()` her işlemden sonra tekrar soruyor
(boş girene kadar) — ama bu turlar arasında konuşma BAĞLAMI taşınmıyor, her
mesaj sıfırdan bağımsız sınıflandırılıyor; gerçek çok turlu bir diyalog durum
makinesi (örn. "onu iptal et" gibi önceki mesaja referans veren ifadeler)
hâlâ yok.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from src.connectors.account_registry import resolve_write_account_id, select_account
from src.connectors.google_calendar import GoogleCalendarConnector
from src.core.logging_config import configure_logging, get_logger
from src.core.models import CandidateEvent, CandidateStatus, EventType, IntentType, SourceType
from src.memory.correction_memory import candidate_snapshot, capture_correction_interactively, capture_edit_correction
from src.policies.derivation import VALID_IMPORTANCE_VALUES, derive_and_save_policy
from src.providers.base import EmbeddingProvider, FileInputCapable, LLMProvider
from src.providers.foundry_local import FoundryLocalEmbeddingProvider, FoundryLocalProvider
from src.providers.json_generation import JsonGenerationError, generate_json, generate_json_from_file
from src.rag.policy_retrieval import retrieve_policies_for_event
from src.services.availability import find_conflicts, suggest_alternative_slots
from src.services.extraction import build_candidate_from_fields
from src.services.intent import classify_intent
from src.services.timeutil import (
    DEFAULT_TIMEZONE,
    ensure_timezone,
    format_date_tr,
    parse_clock_time,
    parse_duration_minutes,
)
from src.storage.db import get_connection, init_db

DEFAULT_MEETING_DURATION_MINUTES = 60  # sistem varsayılanı (§9: en düşük öncelik, politika yoksa devreye girer)

logger = get_logger("vertical_prototype")


def _extraction_system_prompt() -> str:
    today = datetime.now().astimezone()
    return (
        "Sen bir takvim asistanısın. Kullanıcının mesajından bir etkinlik bilgisi çıkar.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Göreceli ifadeleri (yarın, önümüzdeki cuma, vb.) bu tarihe göre çöz.\n"
        "KRİTİK KURALLAR:\n"
        "1. Mesajda AÇIKÇA belirtilmeyen hiçbir bilgiyi UYDURMA. Konum, kişi gibi "
        "alanlar mesajda geçmiyorsa null bırak — tahmin etme.\n"
        "2. AMBIGUOUS_FIELDS YALNIZCA SAATİN KENDİSİ NET DEĞİLSE kullanılır — "
        "örn. mesajda 'saat 1', 'saat 3' gibi sabah/öğleden sonra netliği olmayan "
        "bir ifade var ya da hiç saat yok. Mesajda '14:00', 'saat 14'te', 'akşam "
        "8' gibi NET bir saat açıkça belirtiliyorsa, bunu ambiguous SAYMA — "
        "tarihin göreceli bir ifade olması ('yarın', 'gelecek hafta' gibi, bunu "
        "bugünün tarihine göre kendin hesaplaman gerekmesi) TEK BAŞINA "
        "belirsizlik değildir, doğru hesapladıktan sonra ambiguous_fields'e "
        "ekleme. Belirsiz durumda tarihi yine de doğru hesapla ve "
        "start_datetime'a yaz (saat kısmı için en olası tahmini kullan, tarihi "
        "KAYBETME), AYRICA ambiguous_fields listesine \"start_datetime\" ekle.\n"
        "3. Emin olmadığın her alan için tahmin yerine null + ambiguous_fields tercih et.\n"
        "4. duration_minutes HER ZAMAN dakika cinsinden tam sayıdır — mesajda "
        "gün/hafta birimiyle belirtilse bile dakikaya çevir (1 gün = 1440, "
        "1 hafta = 10080). Bu bir teslim tarihi/deadline DEĞİLSE (yalnızca bir "
        "an değil, bir SÜRE belirtiliyorsa) süreyi mutlaka dakikaya çevirip yaz.\n"
        "Örnekler:\n"
        "- 'yarın 14:00'te diş randevum var' -> start_datetime hesaplanır, "
        "ambiguous_fields EKLENMEZ (saat net).\n"
        "- 'yarın saat 9'da toplantım var' -> ambiguous_fields EKLENMEZ (saat net).\n"
        "- 'öğleden sonra randevum var' -> ambiguous_fields'e \"start_datetime\" "
        "EKLENİR (saat net değil).\n"
        "- 'gelecek hafta toplantım var' -> ambiguous_fields'e \"start_datetime\" "
        "EKLENİR (saat hiç yok).\n"
        "- '3 gün sürecek bir konferansa gidiyorum, pazartesi başlıyor' -> "
        "duration_minutes: 4320 (3 * 1440).\n"
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
    fields = generate_json(llm, _extraction_system_prompt(), user_text)
    return build_candidate_from_fields(
        fields,
        source_type=SourceType.CONVERSATION,
        source_references=[],
        source_languages=[],
        extraction_reason="Kullanıcı mesajından doğrudan çıkarıldı (konuşma akışı).",
    )


# CLI (extract_candidate_event, yukarıda) BİLİNÇLİ OLARAK DEĞİŞTİRİLMEDİ — tek
# seferlik input() döngüsü zaten kullanıcının bir sonraki turda yeniden
# yazmasına izin veriyor, çoklu-etkinlik karmaşası orada hiç rapor edilmedi.
# Web chatbox'ta ise serbest metinle "toplantı A yarın 10'da, B de öbür gün
# 14'te" gibi birden fazla etkinlik tarif edilince model ya ikisini birbirine
# karıştırıp saçma bir candidate üretiyordu, ya da (talimata rağmen) bir JSON
# LİSTESİ döndürüp build_candidate_from_fields'in dict.get() çağrısı
# AttributeError ile çöküyordu (canlı testte bulundu — bu çökme
# chat_routes.py'de sessizce yutuluyordu, kullanıcıya hiç yanıt gitmiyordu).
_TEXT_MULTI_EXTRACTION_INSTRUCTION = (
    "Kullanıcının mesajında BİR ya da BİRDEN FAZLA ayrı etkinlik olabilir "
    "(örn. 'toplantı A yarın 10'da, toplantı B da öbür gün 14'te'). Her ayrı "
    "etkinlik için yukarıdaki kurallara göre bilgi çıkar. SADECE geçerli bir "
    "JSON LİSTESİ döndür — tek etkinlik olsa bile [{...}] şeklinde, ASLA "
    "çıplak {...} nesnesi değil — başka hiçbir açıklama ekleme."
)


def extract_candidate_events_from_text(llm: LLMProvider, user_text: str) -> list[CandidateEvent]:
    """`extract_candidate_event`'in ÇOKLU-etkinlik destekleyen karşılığı —
    yalnızca web chatbox'ın serbest metin girişi için (bkz. yukarıdaki not).
    `extract_candidate_events_from_file` ile AYNI "her zaman liste döndür,
    modelin disiplinsizliğine karşı deterministik son kontrol" deseni —
    tek elemanlı liste (çoğunluk durum) çağıranlar için mevcut davranışla
    birebir aynıdır."""
    user_prompt = f"{_TEXT_MULTI_EXTRACTION_INSTRUCTION}\n\nKullanıcının mesajı: {user_text}"
    parsed = generate_json(llm, _extraction_system_prompt(), user_prompt)
    if not isinstance(parsed, list):
        parsed = [parsed]
    if not parsed:
        raise JsonGenerationError("Mesajdan hiçbir etkinlik çıkarılamadı (boş liste döndü).")
    return [
        build_candidate_from_fields(
            fields,
            source_type=SourceType.CONVERSATION,
            source_references=[],
            source_languages=[],
            extraction_reason="Kullanıcı mesajından doğrudan çıkarıldı (konuşma akışı).",
        )
        for fields in parsed
    ]


# Gemini'nin desteklediği, bu özellik için anlamlı dosya türleri (bkz.
# FileInputCapable) — davetiye/bilet/randevu onayı ya fotoğraf ya da PDF
# olarak gelir. chat_routes.py hem `accept=` attribute'u hem de sunucu
# tarafı doğrulama için TEK kaynak olarak bunu kullanır.
ACCEPTED_FILE_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}

_FILE_EXTRACTION_INSTRUCTION = (
    "Ekteki dosya bir davetiye, afiş, bilet, randevu onayı, ekran görüntüsü veya "
    "benzeri bir kaynak olabilir (fotoğraf ya da PDF). İçinde BİR ya da BİRDEN "
    "FAZLA ayrı etkinlik olabilir (örn. bir haftalık program, birden fazla "
    "toplantı listesi). Her ayrı etkinlik için yukarıdaki kurallara göre bilgi "
    "çıkar. SADECE geçerli bir JSON LİSTESİ döndür — tek etkinlik olsa bile "
    "[{...}] şeklinde, ASLA çıplak {...} nesnesi değil — başka hiçbir açıklama "
    "ekleme."
)


def extract_candidate_events_from_file(
    llm: FileInputCapable, file_bytes: bytes, mime_type: str, user_text: str = ""
) -> list[CandidateEvent]:
    """`extract_candidate_event`'in dosya (görsel/PDF) girişli, ÇOKLU-etkinlik
    destekleyen karşılığı — bir dosyada (örn. bir günün birden fazla toplantısı,
    canlı testte görüldü) birden fazla ayrı etkinlik olabileceğinden HER ZAMAN
    bir liste döner (tek etkinlikli dosyalarda da tek elemanlı). AYNI sistem
    promptunu (tarih/saat bağlamı + JSON şeması + "uydurma" kuralları) kullanır,
    yalnızca kullanıcının serbest metni yerine `generate_json_from_file` ile
    dosya gönderilir. Kullanıcı fotoğrafla birlikte bir not da yazmışsa (örn.
    "bu İngilizce, TR saatine çevir") talimata eklenir — mail body_text'in
    aksine bu güvenilmeyen bir dış kaynak DEĞİL (kullanıcının kendi yazdığı),
    prompt injection önsözü gerekmez (bkz. src/providers/gemini.py
    _UNTRUSTED_CONTEXT_PREAMBLE)."""
    user_prompt = _FILE_EXTRACTION_INSTRUCTION
    if user_text.strip():
        user_prompt += f"\n\nKullanıcının ek notu: {user_text.strip()}"
    parsed = generate_json_from_file(llm, _extraction_system_prompt(), user_prompt, file_bytes, mime_type)
    if not isinstance(parsed, list):
        # Prompttaki "her zaman liste döndür" talimatına rağmen model tek
        # etkinlikli bir dosyada yine de çıplak bir nesne döndürebiliyor
        # (canlı testte görüldü) — talimata güvenmek yerine deterministik
        # bir son kontrol.
        parsed = [parsed]
    if not parsed:
        raise JsonGenerationError("Dosyadan hiçbir etkinlik çıkarılamadı (boş liste döndü).")
    return [
        build_candidate_from_fields(
            fields,
            source_type=SourceType.CONVERSATION,
            source_references=[],
            source_languages=[],
            extraction_reason="Yüklenen dosyadan (görsel/PDF) çıkarıldı.",
        )
        for fields in parsed
    ]


MAX_CLARIFICATION_ATTEMPTS = 3


def apply_retrieved_policies(
    candidate: CandidateEvent,
    embedding_provider: EmbeddingProvider,
    sender: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """Rule Engine adımı: RAG'ın getirdiği politikaları deterministik olarak
    uygular (LLM'e "hangi değer" kararını bırakmaz, bkz. §9/§11). Yalnızca
    hâlâ eksik olan alanlara dokunur — kullanıcının bu mesajda açıkça verdiği
    bilgiyi asla ezmez. ``sender`` verilirse (mail kaynaklı candidate'lar)
    sender-scope'lu politikalar da retrieval'a dahil edilir. ``user_id``
    verilirse (Web UI) yalnızca o kullanıcının politikaları aranır (bkz. plan
    "Per-user isolation") — CLI ``None`` bırakır (eski, sahiplikten bağımsız
    davranış).

    Uygulanan her kural için "(Kural uygulandı: ...)" satırını hem `print()`
    eder (CLI, davranış değişmiyor) HEM DE listeye ekleyip döner — web
    chatbox'ı (`src/services/chat_flow.py`) bu print() çıktısını göremez,
    kendi sohbet balonuna basması için dönüş değerine ihtiyacı var. İki
    mevcut çağrı noktası da (`scan_inbox.py`, bu dosyada `review_and_confirm_candidate`)
    dönüş değerini kullanmıyor, bu yüzden imza değişikliği geriye dönük güvenli."""
    applied_messages: list[str] = []
    policies = retrieve_policies_for_event(
        embedding_provider, candidate.event_type, sender=sender, top_k=5, user_id=user_id
    )

    if not candidate.duration_minutes:  # None veya 0 — bkz. fill_missing_fields_interactively'deki not
        for policy in policies:
            minutes = policy.structured_action.get("default_duration_minutes")
            if minutes:
                candidate.duration_minutes = int(minutes)
                candidate.missing_fields = [f for f in candidate.missing_fields if f != "duration_minutes"]
                candidate.retrieved_policy_ids.append(policy.policy_id)
                message = f'(Kural uygulandı: "{policy.natural_language_rule}")'
                print(message)
                applied_messages.append(message)
                break

    if not candidate.reminders:
        for policy in policies:
            minutes_before = policy.structured_action.get("reminder_minutes_before")
            if minutes_before:
                candidate.reminders = [{"minutes_before": int(minutes_before)}]
                candidate.retrieved_policy_ids.append(policy.policy_id)
                message = f'(Kural uygulandı: "{policy.natural_language_rule}")'
                print(message)
                applied_messages.append(message)
                break

    if candidate.importance is None:
        for policy in policies:
            importance = policy.structured_action.get("importance")
            if importance:
                try:
                    candidate.importance = importance
                except Exception:
                    continue  # eski/bozuk bir politika kaydı olabilir, sonraki adaya geç
                candidate.retrieved_policy_ids.append(policy.policy_id)
                message = f'(Kural uygulandı: "{policy.natural_language_rule}")'
                print(message)
                applied_messages.append(message)
                break

    return applied_messages


def fill_missing_fields_interactively(candidate: CandidateEvent) -> None:
    """Minimal, tek adımlı soru döngüsü. Tam Conversation Layer Hafta 3 kapsamı.
    Politika uygulaması (apply_retrieved_policies) buradan ÖNCE, main()'de,
    koşulsuz çalıştırılır — böylece süre zaten biliniyor olsa bile hatırlatıcı/
    önem gibi diğer politikalar devreye girer."""
    if "title" in candidate.missing_fields:
        candidate.title = input("Etkinliğin başlığı ne olsun? ").strip()

    if "duration_minutes" in candidate.missing_fields:
        if candidate.duration_minutes:
            # NOT: "is not None" DEĞİL — model bazen duration_minutes=0
            # döndürüyor (canlı testte görüldü), 0 dakikalık bir etkinlik
            # anlamsız; "not getattr(...)" ile tutarlı olması için burada da
            # 0'ı "eksik" sayan aynı truthy kontrolü kullanılıyor. Aksi halde
            # bu blok sessizce hiçbir şey yapmadan geçiyor, fonksiyon sonunda
            # missing_fields yeniden hesaplanırken duration_minutes yine
            # "eksik" damgası yiyor ve mail hiç sorulmadan atlanıyordu.
            pass  # RAG/Policy Store'dan geldi (apply_retrieved_policies main()'de çalıştı)
        elif candidate.event_type == EventType.MEETING.value or candidate.event_type == EventType.MEETING:
            candidate.duration_minutes = DEFAULT_MEETING_DURATION_MINUTES
            print(
                f"(Sistem varsayılanı: toplantılar için {DEFAULT_MEETING_DURATION_MINUTES} dakika — "
                "henüz kendi kuralınızı tanımlamadınız.)"
            )
        else:
            for _ in range(MAX_CLARIFICATION_ATTEMPTS):
                raw = input("Süre ne kadar? (örn: 30, 1 saat, 2 gün) ").strip()
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
        for attempt in range(MAX_CLARIFICATION_ATTEMPTS):
            raw = input("Saat belirsiz görünüyor — tam olarak kaçta? (örn: 13:00) ").strip()
            clock = parse_clock_time(raw) if raw else None
            logger.debug(
                "ambiguous-time clarification attempt %d: raw=%r parsed_clock=%r current_start=%r",
                attempt + 1, raw, clock, candidate.start_datetime,
            )
            if not clock:
                print("Saati anlayamadım, tekrar dener misiniz? (örn: 13:00, 13.30, 'saat 9')")
                continue
            if isinstance(candidate.start_datetime, datetime):
                date_part = candidate.start_datetime.date().isoformat()
            else:
                date_part = (candidate.start_datetime or datetime.now().date().isoformat())[:10]
            candidate_value = f"{date_part}T{clock}:00"
            try:
                candidate.start_datetime = ensure_timezone(candidate_value)
                logger.debug("ambiguous-time resolved: %r -> %r", candidate_value, candidate.start_datetime)
                break
            except Exception as e:
                logger.warning("ambiguous-time assignment failed for %r: %s", candidate_value, e)
                print("Bir sorun oldu, tekrar dener misiniz?")

    candidate.missing_fields = [
        name
        for name in ("title", "start_datetime", "duration_minutes")
        if not getattr(candidate, name)
    ]
    candidate.ambiguous_fields = [
        name for name in candidate.ambiguous_fields if not getattr(candidate, name, None)
    ]
    logger.debug(
        "fill_missing_fields_interactively done: missing_fields=%s ambiguous_fields=%s start_datetime=%r duration_minutes=%r",
        candidate.missing_fields, candidate.ambiguous_fields, candidate.start_datetime, candidate.duration_minutes,
    )
    candidate.status = (
        CandidateStatus.NEEDS_INFORMATION
        if (candidate.missing_fields or candidate.ambiguous_fields)
        else CandidateStatus.READY_FOR_CONFIRMATION
    )


def edit_candidate_field_interactively(candidate: CandidateEvent) -> str | None:
    """Kullanıcının önizlemede bir alanı doğrudan düzeltmesini sağlar (§7:
    "kullanıcının bir öneriyi düzenlemesi" de bir user_correction sinyalidir).
    Yalnızca `PersonalPolicy.structured_action`'a eşlenen alanlar (süre/önem)
    ACM'nin "gelecekte de uygula" akışını tetikleyebilir — döndürülen değer
    o durumda structured_action anahtarı (örn. "default_duration_minutes"),
    aksi halde None (başlık/saat/konum düzenlemesi bir "kural" değildir)."""
    choice = input("Hangi alanı düzenlemek istersiniz? (başlık/saat/süre/önem/konum) ").strip().lower()

    if choice in ("başlık", "baslik", "title"):
        candidate.title = input("Yeni başlık: ").strip()
        return None

    if choice in ("saat", "tarih", "start_datetime"):
        for _ in range(MAX_CLARIFICATION_ATTEMPTS):
            raw = input("Yeni tarih/saat (YYYY-MM-DDTHH:MM:SS): ").strip()
            try:
                candidate.start_datetime = ensure_timezone(raw or None)
                return None
            except Exception:
                print("Bu formatı anlayamadım, YYYY-MM-DDTHH:MM:SS biçiminde tekrar dener misiniz?")
        return None

    if choice in ("süre", "sure", "duration_minutes"):
        for _ in range(MAX_CLARIFICATION_ATTEMPTS):
            raw = input("Yeni süre (örn: 30, 1 saat): ").strip()
            minutes = parse_duration_minutes(raw)
            if minutes:
                candidate.duration_minutes = minutes
                return "default_duration_minutes"
            print("Anlayamadım, bir sayı içeren şekilde tekrar dener misiniz? (örn: 45 veya '1 saat')")
        return None

    if choice in ("önem", "onem", "importance"):
        raw = input("Yeni önem (low/normal/high): ").strip().lower()
        if raw in VALID_IMPORTANCE_VALUES:
            candidate.importance = raw
            return "importance"
        print("Geçersiz değer (low/normal/high olmalı), önem değiştirilmedi.")
        return None

    if choice in ("konum", "location"):
        candidate.location = input("Yeni konum: ").strip() or None
        return None

    print("Anlamadım, hiçbir şey değiştirilmedi.")
    return None


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


def set_candidate_google_event_id(conn, candidate_id: str, event_id: str) -> None:
    """`candidates/store.py::set_candidate_google_event_id`'nin conn-paylaşımlı
    (CLI/web sohbet akışı) karşılığı — mail kaynaklı bir güncelleme önerisi
    onaylandığında `update_event`'e hangi Google etkinliğinin hedefleneceğini
    bilebilmek için bu id'nin ilk kez burada saklanması gerekiyor."""
    conn.execute(
        "UPDATE candidate_events SET google_event_id = ?, updated_at = ? WHERE candidate_id = ?",
        (event_id, datetime.now(timezone.utc).isoformat(), candidate_id),
    )


def record_audit(conn, action: str, entity_id: str, reason: str, entity_type: str = "candidate_event") -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (id, actor, action, entity_type, entity_id, reason, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            "user",
            action,
            entity_type,
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

    reminders_desc = (
        ", ".join(f"{r.minutes_before} dk önce" for r in candidate.reminders)
        if candidate.reminders
        else "(yok)"
    )

    print("\n--- Önizleme ---")
    print(f"Başlık:     {candidate.title}")
    print(f"Zaman:      {start} -> {end}")
    print(f"Konum:      {candidate.location or '(belirtilmedi)'}")
    print(f"Tür:        {candidate.event_type}")
    print(f"Önem:       {candidate.importance or '(belirtilmedi)'}")
    print(f"Hatırlatıcı: {reminders_desc}")
    print(f"Çakışma:    {conflict_note}")
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

    # end_dt başlangıçla AYNI günde değilse (örn. çok günlü bir tatil)
    # yalnızca saat göstermek "08:00-08:00" gibi anlamsız/sıfır-süreli
    # görünüyordu — tarih FARKLI olduğunda burada da gösteriliyor (bkz.
    # aynı köklü hata web tarafında da bulundu, formatting.py::
    # format_end_time_or_datetime).
    end_display = (
        f"{end_dt:%H:%M}" if end_dt.date() == start_dt.date() else f"{format_date_tr(end_dt)}, {end_dt:%H:%M}"
    )
    print(f"\n⚠ Çakışma bulundu: {start_dt:%H:%M}-{end_display} aralığında zaten bir etkinliğiniz var.")
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


def handle_define_policy(llm: LLMProvider, embedding_provider: EmbeddingProvider, user_text: str) -> None:
    policy = derive_and_save_policy(llm, embedding_provider, user_text)
    if policy is None:
        print("Bu kuraldan somut bir davranış çıkaramadım — biraz daha net ifade eder misiniz?")
        return

    event_type = policy.structured_conditions.get("event_type")
    scope_desc = f"'{event_type}' türü etkinlikler" if event_type else "tüm etkinlikler"
    print(f"Kaydettim: {scope_desc} için '{user_text}' kuralı artık aktif.")


def handle_query_calendar(account_id: str, range_start: datetime | None, range_end: datetime | None) -> None:
    if range_start is None or range_end is None:
        print("Hangi tarih aralığını merak ediyorsunuz, tam olarak söyler misiniz?")
        return

    calendar = GoogleCalendarConnector(account_id=resolve_write_account_id(account_id))
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


def _update_event_extraction_system_prompt(today: datetime) -> str:
    return (
        "Sen bir takvim asistanısın. Kullanıcı VAR OLAN bir etkinliği "
        "değiştirmek/taşımak/iptal etmek istiyor. Mesajdan bilgi çıkar.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Göreceli ifadeleri (yarın, önümüzdeki cuma, vb.) bu tarihe göre çöz.\n"
        "KRİTİK KURALLAR:\n"
        "1. 'title_hint': kullanıcının bahsettiği etkinliğin başlığıyla ilgili "
        "anahtar kelime(ler) — tam başlığı bilmiyor olabilir, mesajda ne "
        "geçiyorsa onu yaz. Hiçbir ipucu yoksa null.\n"
        "2. 'date_hint': kullanıcının kastettiği GÜN, YYYY-MM-DD olarak. "
        "Belirsizse/hiç geçmiyorsa null — UYDURMA.\n"
        "3. 'cancel': kullanıcı etkinliği TAMAMEN İPTAL ETMEK/SİLMEK istiyorsa "
        "true; yalnızca SAAT/GÜN DEĞİŞTİRMEK istiyorsa false.\n"
        "4. cancel=false ise 'new_start_datetime' (YYYY-MM-DDTHH:MM:SS) ve "
        "varsa 'new_duration_minutes' doldur — mesajda AÇIKÇA belirtilmeyeni "
        "UYDURMA, null bırak.\n"
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme. Alanlar:\n"
        '{"title_hint": string veya null, "date_hint": "YYYY-MM-DD" veya null, '
        '"cancel": true veya false, "new_start_datetime": "YYYY-MM-DDTHH:MM:SS" '
        'veya null, "new_duration_minutes": integer veya null}'
    )


def _find_matching_events(
    calendar: GoogleCalendarConnector, title_hint: str | None, date_hint: str | None
) -> list[dict]:
    """Değiştirilecek etkinliği CANLI olarak Google Calendar'da arar
    (calendar_events_cache kullanılmıyor — hiç doldurulmuyor, ayrı bir iş).
    Eşleştirme deterministik: date_hint varsa o günün tamamı, yoksa önümüzdeki
    14 gün taranır; title_hint varsa summary içinde büyük/küçük harf duyarsız
    alt-metin eşleşmesiyle daraltılır (LLM'e "hangisi doğru" dedirtilmez —
    bkz. §11)."""
    if date_hint:
        window_start = ensure_timezone(f"{date_hint}T00:00:00")
        window_end = window_start + timedelta(days=1)
    else:
        window_start = datetime.now().astimezone()
        window_end = window_start + timedelta(days=14)

    events = calendar.list_events(window_start, window_end)
    if title_hint:
        needle = title_hint.strip().lower()
        matched = [e for e in events if needle in (e.get("summary") or "").lower()]
        if matched:
            return matched
    return events


def handle_update_event(llm: LLMProvider, account_id: str, user_text: str) -> None:
    today = datetime.now().astimezone()
    fields = generate_json(llm, _update_event_extraction_system_prompt(today), user_text)

    calendar = GoogleCalendarConnector(account_id=resolve_write_account_id(account_id))
    candidates = _find_matching_events(calendar, fields.get("title_hint"), fields.get("date_hint"))

    if not candidates:
        print("Değiştirmek istediğiniz etkinliği bulamadım. Daha net tarif eder misiniz (başlık/tarih)?")
        return

    if len(candidates) > 1:
        shortlist = candidates[:5]
        print("Birden fazla etkinlik buldum, hangisini kastediyorsunuz?")
        for i, e in enumerate(shortlist, 1):
            start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
            print(f"  {i}. {e.get('summary', '(başlıksız)')} | {start}")
        choice = input(f"Seçin (1-{len(shortlist)}) veya iptal (i): ").strip().lower()
        if not choice.isdigit() or not (1 <= int(choice) <= len(shortlist)):
            print("İptal edildi, hiçbir değişiklik yapılmadı.")
            return
        event = shortlist[int(choice) - 1]
    else:
        event = candidates[0]

    event_id = event["id"]
    old_summary = event.get("summary", "(başlıksız)")
    old_start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")

    if fields.get("cancel"):
        print(f"\n'{old_summary}' ({old_start_raw}) etkinliğini SİLMEK üzeresiniz.")
        confirm = input("Onaylıyor musunuz? [e/h] ").strip().lower()
        if confirm != "e":
            print("İptal edildi, hiçbir değişiklik yapılmadı.")
            return
        calendar.delete_event(event_id)
        with get_connection() as conn:
            record_audit(
                conn, "delete_event", event_id, f"Kullanıcı isteğiyle silindi: {user_text}",
                entity_type="calendar_event",
            )
        print("Etkinlik silindi.")
        return

    new_start_raw = fields.get("new_start_datetime")
    if not new_start_raw:
        print("Yeni tarih/saati anlayamadım, tekrar ifade eder misiniz?")
        return
    new_start = ensure_timezone(new_start_raw)

    duration = fields.get("new_duration_minutes")
    if not duration:
        old_end_raw = event.get("end", {}).get("dateTime")
        if old_start_raw and old_end_raw:
            duration = int(
                (datetime.fromisoformat(old_end_raw) - datetime.fromisoformat(old_start_raw)).total_seconds() / 60
            )
        else:
            duration = DEFAULT_MEETING_DURATION_MINUTES
    new_end = new_start + timedelta(minutes=duration)

    conflicts = find_conflicts(calendar, new_start, new_end)
    # Etkinliğin kendi eski zamanı, yeni aralıkla örtüşüyorsa (örn. süre
    # uzatılırken) freebusy'de "meşgul" olarak görünebilir — gerçek bir
    # çakışma değil, kendisiyle çakışma sayılmasın diye eski başlangıcıyla
    # tam örtüşen sonucu göz ardı ediyoruz.
    if old_start_raw:
        conflicts = [c for c in conflicts if c[0].isoformat() != old_start_raw]

    print(f"\n'{old_summary}' ({old_start_raw}) -> {new_start.isoformat()} olarak taşınacak.")
    if conflicts:
        print(f"⚠ Yeni saatte zaten başka bir etkinliğiniz var ({len(conflicts)} çakışma).")
    confirm = input("Onaylıyor musunuz? [e/h] ").strip().lower()
    if confirm != "e":
        print("İptal edildi, hiçbir değişiklik yapılmadı.")
        return

    calendar.update_event(event_id, start=new_start, end=new_end)
    with get_connection() as conn:
        record_audit(
            conn, "update_event", event_id, f"Kullanıcı isteğiyle güncellendi: {user_text}",
            entity_type="calendar_event",
        )
    print("Etkinlik güncellendi.")


def review_and_confirm_candidate(
    candidate: CandidateEvent,
    calendar: GoogleCalendarConnector,
    embedding_provider: EmbeddingProvider,
    llm: LLMProvider,
    source_email_row_id: str | None = None,
    source_sender: str | None = None,
    source_email_text: str | None = None,
) -> None:
    """Politika uygulama + eksik/belirsiz alan tamamlama + çakışma kontrolü +
    önizleme + onay + (onaylanırsa) takvime yazma. Konuşma akışı (main()) ve
    mail taraması (scan_inbox.py) tarafından ortak kullanılır. Reddedilirse
    Adaptive Correction Memory'ye düşer (bkz. capture_correction_interactively).
    ``source_sender`` yalnızca mail akışında verilir (email.sender) — hem
    sender-scope'lu politika retrieval'ı hem ACM'nin scope seçimi için.
    ``source_email_text`` (mail_analysis.build_email_text) verilirse ACM
    kullanıcıya "bu mail hiç takvimlik değil miydi?" seçeneğini de sunar."""
    apply_retrieved_policies(candidate, embedding_provider, sender=source_sender)

    if candidate.missing_fields or candidate.ambiguous_fields:
        fill_missing_fields_interactively(candidate)

    if candidate.missing_fields or candidate.ambiguous_fields:
        print("Gerekli bilgileri tamamlayamadım, atlıyorum.")
        return

    conflict_note = "Yok"
    if candidate.start_datetime and candidate.duration_minutes:
        conflict_note = resolve_conflicts_interactively(calendar, candidate)

    with get_connection() as conn:
        save_candidate(conn, candidate)
        if source_email_row_id:
            conn.execute(
                "INSERT INTO candidate_sources (candidate_id, email_message_id, relation_type, created_at) "
                "VALUES (?,?,?,?)",
                (
                    candidate.candidate_id,
                    source_email_row_id,
                    "origin",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    if conflict_note == "Var (iptal edilecek)":
        with get_connection() as conn:
            update_candidate_status(conn, candidate.candidate_id, CandidateStatus.REJECTED)
            record_audit(conn, "reject", candidate.candidate_id, "Çözülmeyen çakışma nedeniyle iptal.")
        print("İptal edildi, takvime yazılmadı.")
        return

    # Düzenleme öncesi anlık görüntü: kullanıcı bir alanı değiştirirse ACM'nin
    # gerçek bir "önce/sonra" farkı görebilmesi için (bkz. capture_edit_correction).
    original_snapshot = candidate_snapshot(candidate)
    edited_structured_action: dict = {}

    while True:
        print_preview(candidate, conflict_note)
        approval = input("Onaylıyor musunuz? [e/h/d] ").strip().lower()
        if approval == "d":
            edited_category = edit_candidate_field_interactively(candidate)
            if edited_category:
                edited_structured_action[edited_category] = getattr(
                    candidate, "duration_minutes" if edited_category == "default_duration_minutes" else "importance"
                )
            if candidate.start_datetime and candidate.duration_minutes:
                conflict_note = resolve_conflicts_interactively(calendar, candidate)
            continue
        break

    with get_connection() as conn:
        if approval != "e":
            update_candidate_status(conn, candidate.candidate_id, CandidateStatus.REJECTED)
            record_audit(conn, "reject", candidate.candidate_id, "Kullanıcı reddetti.")
        else:
            start_dt = candidate.start_datetime
            if isinstance(start_dt, str):
                start_dt = datetime.fromisoformat(start_dt)
            end_dt = start_dt + timedelta(minutes=candidate.duration_minutes or DEFAULT_MEETING_DURATION_MINUTES)

            event_id = calendar.create_event(
                title=candidate.title, start=start_dt, end=end_dt, location=candidate.location
            )

            update_candidate_status(conn, candidate.candidate_id, CandidateStatus.ADDED_TO_CALENDAR)
            set_candidate_google_event_id(conn, candidate.candidate_id, event_id)
            record_audit(conn, "approve_and_write", candidate.candidate_id, f"Google Calendar event_id={event_id}")

    # ACM yakalaması bu `with` bloğu KAPANDIKTAN sonra çağrılıyor: kendi
    # get_connection() çağrılarını yapıyor (save_user_correction, add_policy,
    # ...), yukarıdaki bağlantı hâlâ açıkken (bekleyen bir yazma işlemiyle)
    # ikinci bir bağlantı açmak SQLite'ta kilitlenme riski taşır.
    if approval != "e":
        print("Reddedildi, takvime yazılmadı.")
        capture_correction_interactively(
            llm, embedding_provider, candidate, sender=source_sender, email_text=source_email_text
        )
        return

    print(f"Takvime eklendi. event_id={event_id}")

    if edited_structured_action:
        capture_edit_correction(
            embedding_provider, candidate, original_snapshot, edited_structured_action, sender=source_sender
        )


def main() -> None:
    configure_logging()
    init_db()
    account_id, _account_email = select_account()
    llm = FoundryLocalProvider(model_alias="qwen3-4b")
    embedding_provider = FoundryLocalEmbeddingProvider()

    print("(Çıkmak için boş bırakıp Enter'a basın.)")
    while True:
        user_text = input("\nNe planlamak istiyorsunuz? ").strip()
        if not user_text:
            break

        intent = classify_intent(llm, user_text)

        if intent.intent == IntentType.QUERY_CALENDAR:
            handle_query_calendar(account_id, intent.query_range_start, intent.query_range_end)
            continue
        if intent.intent == IntentType.UPDATE_EVENT:
            try:
                handle_update_event(llm, account_id, user_text)
            except JsonGenerationError:
                print("Bu isteği işleyemedim, tekrar ifade eder misiniz?")
            continue
        if intent.intent == IntentType.DEFINE_POLICY:
            try:
                handle_define_policy(llm, embedding_provider, user_text)
            except JsonGenerationError:
                print("Bu kuralı işleyemedim, biraz daha net ifade edip tekrar dener misiniz?")
            continue
        if intent.intent == IntentType.OTHER:
            print("Bunu tam anlayamadım. Şu an yeni etkinlik eklemek ve takviminizi sormak için kullanılabilirim.")
            continue

        try:
            candidate = extract_candidate_event(llm, user_text)
        except JsonGenerationError:
            print("Bu mesajı işleyemedim, tekrar ifade eder misiniz?")
            continue
        calendar = GoogleCalendarConnector(account_id=resolve_write_account_id(account_id))
        review_and_confirm_candidate(candidate, calendar, embedding_provider, llm)


if __name__ == "__main__":
    main()
