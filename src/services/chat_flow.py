"""Web chatbox durum makinesi (bkz. plan "Web Chatbox").

CLI'nın `vertical_prototype.py`'sindeki konuşma akışının (create_event +
query_calendar) `input()`'suz karşılığı. HTTP istekleri durumsuz olduğundan,
CLI'da `input()` çağrıları arasında Python yığınında canlı kalan yerel
değişkenler (kısmen dolu `CandidateEvent`, hangi netleştirme adımında
olduğumuz, deneme sayaçları, gösterilen alternatif saat listesi) burada
açık bir `ChatState` nesnesine taşınıyor — her POST'ta önceki state + yeni
kullanıcı metniyle `advance()` çağrılır, yeni state + asistan mesajları
döner. Kalıcılık (SQLite) bu modülün İŞİ DEĞİL — `src/ui/chat_state.py`
`ChatState`'i `chat_sessions.state_json`'a (de)serileştirir. `calendar`/
`llm`/`embedding_provider` bağımlılık enjeksiyonuyla alınır (test
edilebilirlik için — sahte bir takvim/LLM ile sürülebilir).

SQL yazma noktaları (`save_candidate`/`update_candidate_status`/
`record_audit`) KENDİ kısa ömürlü `get_connection()`'ını açar — `conn` dışarıdan
enjekte edilmiyor (bkz. canlı testte bulunan bug: `chat_state.py::process_message`
eskiden TÜM `advance()` çağrısını — LLM/takvim API çağrıları dahil — tek bir
`get_connection()` transaction'ı içine alıyordu; CPU'da 10-30sn süren bir LLM
çağrısı boyunca SQLite yazma kilidi açık kalınca, aynı anda gelen başka bir
istek (örn. "Yeni sohbet" sıfırlama) `sqlite3.OperationalError: database is
locked` ile 500 veriyordu. `vertical_prototype.py`'nin kendisi de zaten HER
yazma noktasında ayrı, kısa bir `get_connection()` açıyor — burada da aynı
desene dönüldü, tek bir uzun ömürlü bağlantı asla LLM/takvim çağrısı boyunca
açık tutulmuyor)."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from src.core.logging_config import get_logger
from src.core.models import CandidateEvent, CandidateStatus, CorrectionScope, EventType, IntentType, PolicySource
from src.localization import translate
from src.localization.formatting import format_datetime, format_end_time_or_datetime, format_time
from src.memory.correction_memory import (
    _STRUCTURED_ACTION_LABELS,
    candidate_snapshot,
    mark_correction_approved,
    save_user_correction,
)
from src.policies.derivation import VALID_IMPORTANCE_VALUES, derive_and_save_policy, save_derived_policy
from src.providers.base import EmbeddingProvider, FileInputCapable, LLMProvider
from src.providers.json_generation import JsonGenerationError, generate_json
from src.services.availability import find_conflicts, suggest_alternative_slots
from src.services.calendar_view import parse_google_event
from src.services.intent import classify_intent
from src.services.timeutil import DEFAULT_TIMEZONE, ensure_timezone, parse_clock_time, parse_duration_minutes
from src.services.vertical_prototype import (
    ACCEPTED_FILE_MIME_TYPES,
    MAX_CLARIFICATION_ATTEMPTS,
    DEFAULT_MEETING_DURATION_MINUTES,
    _find_matching_events,
    _update_event_extraction_system_prompt,
    apply_retrieved_policies,
    extract_candidate_events_from_file,
    extract_candidate_events_from_text,
    record_audit,
    save_candidate,
    set_candidate_google_event_id,
    update_candidate_status,
)
from src.storage.db import get_connection

logger = get_logger("chat_flow")

FLOW_CREATE_EVENT = "create_event"
FLOW_UPDATE_EVENT = "update_event"

STEP_UPDATE_DISAMBIGUATE = "update_disambiguate"
STEP_UPDATE_CONFIRM_DELETE = "update_confirm_delete"
STEP_UPDATE_CONFIRM_MOVE = "update_confirm_move"

STEP_ASK_TITLE = "ask_title"
STEP_ASK_DURATION = "ask_duration"
STEP_ASK_START_DATETIME = "ask_start_datetime"
STEP_ASK_AMBIGUOUS_TIME = "ask_ambiguous_time"
STEP_ASK_CONFLICT_NO_ALTERNATIVES = "ask_conflict_no_alternatives"
STEP_ASK_CONFLICT_ALTERNATIVE = "ask_conflict_alternative"
STEP_PREVIEW_CONFIRM = "preview_confirm"
STEP_EDIT_PICK_FIELD = "edit_pick_field"
STEP_EDIT_FIELD_VALUE = "edit_field_value"

# ACM "gelecekte de uygulayayım mı?" akışı (bkz. capture_correction_interactively/
# capture_edit_correction, vertical_prototype.py) — create_event akışının reddetme
# ya da düzenleme-sonrası-onay sonuçlarının bir DEVAMI, ayrı bir flow değil (CLI'da
# da review_and_confirm_candidate'in kendisi bu yakalamayı çağırıyor).
STEP_ACM_ASK_REJECT_FEEDBACK = "acm_ask_reject_feedback"
STEP_ACM_ASK_APPLY_FUTURE = "acm_ask_apply_future"
STEP_ACM_ASK_SCOPE = "acm_ask_scope"

ACM_KIND_REJECT = "reject"
ACM_KIND_EDIT = "edit"

# Dahili işaret — _resolve_create_event_state'in "çakışma kontrolüne hazır"
# dönüşü, asla kalıcı bir `state.step` olarak saklanmaz (_continue_create_event
# bunu aynı turda tüketir).
_READY_FOR_CONFLICT_CHECK = "_ready_for_conflict_check"

CONFLICT_NONE = "none"
CONFLICT_KEPT_ANYWAY = "kept_anyway"
CONFLICT_UNRESOLVED = "unresolved"
CONFLICT_MOVED = "moved"
CONFLICT_CANCELLED = "cancelled"

_STEP_PROMPT_KEYS = {
    STEP_ASK_TITLE: "chat.create.ask_title",
    STEP_ASK_DURATION: "chat.create.ask_duration",
    STEP_ASK_START_DATETIME: "chat.create.ask_start_datetime",
    STEP_ASK_AMBIGUOUS_TIME: "chat.create.ask_ambiguous_time",
}

_EDIT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("başlık", "baslik", "title"),
    "start_datetime": ("saat", "tarih", "start_datetime"),
    "duration_minutes": ("süre", "sure", "duration_minutes"),
    "importance": ("önem", "onem", "importance"),
    "location": ("konum", "location"),
}


class ChatState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    flow: str | None = None
    step: str | None = None
    candidate: CandidateEvent | None = None
    attempts: int = 0
    alternatives: list[datetime] = Field(default_factory=list)
    conflict_note: str = CONFLICT_NONE
    candidate_saved: bool = False
    edited_structured_action: dict = Field(default_factory=dict)
    pending_edit_field: str | None = None
    # ACM "gelecekte de uygulayayım mı?" akışı için: original_snapshot ilk
    # çakışma çözümü SONUÇLANDIĞINDA (candidate_saved False->True olurken,
    # bkz. _enter_preview) bir kez alınır — CLI'daki review_and_confirm_candidate
    # ile AYNI nokta (satır 654'teki original_snapshot = candidate_snapshot(candidate)).
    original_snapshot: dict | None = None
    acm_kind: str | None = None  # ACM_KIND_REJECT | ACM_KIND_EDIT
    acm_feedback: str | None = None
    acm_correction_id: str | None = None
    # Dosyadan çoklu-etkinlik çıkarımı (bkz. _dispatch_file_upload,
    # extract_candidate_events_from_file) — bir dosyada birden fazla ayrı
    # etkinlik tespit edilirse, `candidate` bunlardan İLKİ olur ve geri kalanı
    # burada bekler. Her candidate TAMAMEN bitince (onay/red/hata, bkz.
    # _finish_candidate) sıradaki otomatik başlatılır. Tekli (dosyasız ya da
    # tek-etkinlikli) akışlarda queued_candidates hep boş kalır, batch_total=0
    # — davranış hiç değişmez.
    queued_candidates: list[CandidateEvent] = Field(default_factory=list)
    batch_index: int = 0  # şu an işlenen adayın 1-tabanlı sırası (0 = batch değil)
    batch_total: int = 0  # toplam aday sayısı (0 = batch değil)
    # update_event
    update_fields: dict | None = None
    shortlist: list[dict] | None = None
    matched_event: dict | None = None


def advance(
    state: ChatState,
    user_text: str,
    *,
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    calendar,
    lang: str,
    file_bytes: bytes | None = None,
    file_mime_type: str | None = None,
) -> tuple[ChatState, list[str]]:
    """(yeni_state, asistan_mesajları) döner. Bir adım birden fazla satır
    üretebilir (örn. "(Kural uygulandı: ...)" + önizleme metni).

    ``file_bytes``/``file_mime_type`` (bkz. FileInputCapable): yalnızca
    TAZE bir sohbette (``state.flow is None``) anlamlı — devam eden bir
    netleştirme/onay adımı sırasında bir dosya eklenirse sessizce yok
    sayılır (o adımlar zaten belirli kısa yanıtlar bekliyor, bir dosyayı
    nasıl yorumlayacağı tanımsız olurdu)."""
    if state.flow is None:
        if file_bytes is not None:
            return _dispatch_file_upload(
                file_bytes, file_mime_type or "application/octet-stream", user_text,
                llm=llm, embedding_provider=embedding_provider, calendar=calendar, lang=lang,
            )
        return _dispatch_intent(
            user_text, llm=llm, embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )
    if state.flow == FLOW_CREATE_EVENT:
        return _advance_create_event(
            state, user_text, llm=llm, embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )
    if state.flow == FLOW_UPDATE_EVENT:
        return _advance_update_event(state, user_text, calendar=calendar, lang=lang)
    logger.warning("Bilinmeyen flow: %r — oturum sıfırlanıyor", state.flow)
    return ChatState(), [translate("chat.generic_error", lang)]


def _dispatch_intent(
    user_text: str, *, llm, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    intent = classify_intent(llm, user_text)

    if intent.intent == IntentType.QUERY_CALENDAR:
        return _handle_query_calendar(intent.query_range_start, intent.query_range_end, calendar=calendar, lang=lang)

    if intent.intent == IntentType.CREATE_EVENT:
        try:
            candidates = extract_candidate_events_from_text(llm, user_text)
        except JsonGenerationError:
            return ChatState(), [translate("chat.create.extraction_failed", lang)]

        # Mesajda birden fazla ayrı etkinlik tarif edilmiş olabilir (bkz.
        # vertical_prototype.py::extract_candidate_events_from_text) — dosya
        # yükleme akışıyla (_dispatch_file_upload) AYNI "ilkiyle başla, geri
        # kalanı queued_candidates'te bekle" deseni, tek-etkinlikli mesajlarda
        # (çoğunluk durum) davranış hiç değişmez.
        first, *rest = candidates
        batch_total = len(candidates)
        applied_messages = apply_retrieved_policies(first, embedding_provider)
        initial_state = ChatState(
            flow=FLOW_CREATE_EVENT,
            candidate=first,
            queued_candidates=rest,
            batch_index=1 if batch_total > 1 else 0,
            batch_total=batch_total if batch_total > 1 else 0,
        )
        return _continue_create_event(initial_state, lang=lang, calendar=calendar, embedding_provider=embedding_provider, prefix_messages=applied_messages)

    if intent.intent == IntentType.UPDATE_EVENT:
        return _start_update_event(user_text, llm=llm, calendar=calendar, lang=lang)

    if intent.intent == IntentType.DEFINE_POLICY:
        return _handle_define_policy(user_text, llm=llm, embedding_provider=embedding_provider, lang=lang)

    return ChatState(), [translate("chat.other", lang)]


# --- dosyadan (görsel/PDF) etkinlik ekleme (bkz. FileInputCapable) ---
# Niyet tespiti YOK: bir dosya eklemek zaten tek anlamlı bir niyettir
# ("bundan bir etkinlik çıkar"), classify_intent'e sormaya gerek yok.
# extract_candidate_events_from_file'dan sonrası _dispatch_intent'in
# CREATE_EVENT dalıyla AYNI (_continue_create_event) — önizleme/çakışma/
# onay akışının tamamı iki giriş yolu arasında paylaşılıyor. Dosyada birden
# fazla ayrı etkinlik bulunursa geri kalanı ChatState.queued_candidates'te
# bekler, her biri _finish_candidate ile sırayla işlenir (bkz. altta).

# Mime/boyut doğrulaması BİLEREK burada (route katmanında değil) — hem
# route'un dosyayı okuyup mime_type'ını çözmekten öte hiçbir iş bilmesi
# gerekmiyor, hem de hata mesajı doğal olarak normal bir asistan balonu
# olarak akışa giriyor (ayrı bir "hata enjekte et" yolu gerekmiyor).
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB — telefon fotoğrafı/taranmış PDF için yeterli, aşırı büyük yüklemeleri eler


def _dispatch_file_upload(
    file_bytes: bytes, mime_type: str, user_text: str, *, llm, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    if not isinstance(llm, FileInputCapable):
        # Varsayılan (Foundry Local) provider dosya girişini desteklemiyor —
        # sessizce yok saymak yerine kullanıcıya NEDEN çalışmadığını söylüyoruz
        # (bkz. LLM_PROVIDER=gemini, .env.example).
        return ChatState(), [translate("chat.file.unsupported_provider", lang)]

    if mime_type not in ACCEPTED_FILE_MIME_TYPES:
        return ChatState(), [translate("chat.file.unsupported_type", lang)]

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        return ChatState(), [translate("chat.file.too_large", lang, max_mb=MAX_FILE_SIZE_BYTES // (1024 * 1024))]

    try:
        candidates = extract_candidate_events_from_file(llm, file_bytes, mime_type, user_text)
    except JsonGenerationError:
        return ChatState(), [translate("chat.file.extraction_failed", lang)]

    # Dosyada birden fazla ayrı etkinlik bulunmuş olabilir (bkz.
    # ChatState.queued_candidates) — ilkiyle başlanır, geri kalanı
    # _finish_candidate her candidate tamamlandığında sırayla işler.
    first, *rest = candidates
    batch_total = len(candidates)
    applied_messages = apply_retrieved_policies(first, embedding_provider)
    initial_state = ChatState(
        flow=FLOW_CREATE_EVENT,
        candidate=first,
        queued_candidates=rest,
        batch_index=1 if batch_total > 1 else 0,
        batch_total=batch_total if batch_total > 1 else 0,
    )
    return _continue_create_event(initial_state, lang=lang, calendar=calendar, embedding_provider=embedding_provider, prefix_messages=applied_messages)


# --- define_policy (tek atımlık, CLI'nın handle_define_policy'siyle aynı — bkz. plan) ---


def _handle_define_policy(
    user_text: str, *, llm, embedding_provider, lang: str
) -> tuple[ChatState, list[str]]:
    try:
        policy = derive_and_save_policy(llm, embedding_provider, user_text)
    except JsonGenerationError:
        return ChatState(), [translate("chat.define_policy.extraction_failed", lang)]

    if policy is None:
        return ChatState(), [translate("chat.define_policy.no_rule_extracted", lang)]

    event_type = policy.structured_conditions.get("event_type")
    scope = (
        translate("chat.acm.scope_event_type_desc", lang, event_type=event_type)
        if event_type
        else translate("chat.acm.scope_always_desc", lang)
    )
    return ChatState(), [translate("chat.define_policy.saved", lang, scope=scope, rule=user_text)]


# --- query_calendar (tek atımlık, DB'ye hiç yazmaz — CLI'daki handle_query_calendar ile aynı) ---


def _handle_query_calendar(range_start, range_end, *, calendar, lang: str) -> tuple[ChatState, list[str]]:
    if range_start is None or range_end is None:
        return ChatState(), [translate("chat.query.no_range", lang)]

    try:
        raw_events = calendar.list_events(range_start, range_end)
    except Exception:
        logger.exception("Takvim sorgusu başarısız (sohbet akışı)")
        return ChatState(), [translate("chat.calendar_error", lang)]

    entries = [entry for raw in raw_events if (entry := parse_google_event(raw, DEFAULT_TIMEZONE)) is not None]

    if not entries:
        return ChatState(), [
            translate(
                "chat.query.no_events", lang,
                start=format_datetime(range_start, lang), end=format_datetime(range_end, lang),
            )
        ]

    lines = [translate("chat.query.events_found", lang, count=len(entries))]
    for entry in entries:
        time_text = translate("chat.query.all_day", lang) if entry.all_day else format_time(entry.start, lang)
        title = entry.title or translate("common.untitled", lang)
        lines.append(f"- {title} ({time_text})")
    return ChatState(), lines


# --- create_event ---


def _resolve_create_event_state(candidate: CandidateEvent, *, lang: str) -> tuple[str, list[str]]:
    """CLI'nın `fill_missing_fields_interactively`'sindeki sabit sıra: title
    -> duration (policy-dolu/MEETING-varsayılan/sor) -> start_datetime
    (eksik) -> start_datetime (belirsiz) -> çakışma kontrolüne hazır.
    Kullanıcı girdisi gerektirmeyenleri (policy-dolu süre, MEETING
    varsayılanı) hemen çözüp devam eder, ilk gerçek soruyu (ya da
    `_READY_FOR_CONFLICT_CHECK`'i) döner."""
    if "title" in candidate.missing_fields:
        return STEP_ASK_TITLE, []

    messages: list[str] = []
    if "duration_minutes" in candidate.missing_fields:
        if candidate.duration_minutes:
            pass  # RAG/Policy Store'dan geldi (apply_retrieved_policies zaten çalıştı)
        elif candidate.event_type in (EventType.MEETING, EventType.MEETING.value):
            candidate.duration_minutes = DEFAULT_MEETING_DURATION_MINUTES
            messages.append(
                translate("chat.create.duration_default", lang, minutes=DEFAULT_MEETING_DURATION_MINUTES)
            )
        else:
            return STEP_ASK_DURATION, messages
        candidate.missing_fields = [f for f in candidate.missing_fields if f != "duration_minutes"]

    if "start_datetime" in candidate.missing_fields:
        return STEP_ASK_START_DATETIME, messages

    if "start_datetime" in candidate.ambiguous_fields:
        return STEP_ASK_AMBIGUOUS_TIME, messages

    return _READY_FOR_CONFLICT_CHECK, messages


def _continue_create_event(
    state: ChatState, *, lang: str, calendar, embedding_provider, prefix_messages: list[str] | None = None
) -> tuple[ChatState, list[str]]:
    prefix_messages = list(prefix_messages or [])
    next_step, extra_messages = _resolve_create_event_state(state.candidate, lang=lang)
    messages = prefix_messages + extra_messages

    if next_step == _READY_FOR_CONFLICT_CHECK:
        return _run_conflict_check(
            state, calendar=calendar, embedding_provider=embedding_provider, lang=lang, prefix_messages=messages
        )

    new_state = state.model_copy(update={"step": next_step, "attempts": 0})
    return new_state, messages + [translate(_STEP_PROMPT_KEYS[next_step], lang)]


def _run_conflict_check(
    state: ChatState, *, calendar, embedding_provider, lang: str, prefix_messages: list[str]
) -> tuple[ChatState, list[str]]:
    candidate = state.candidate
    start_dt = candidate.start_datetime
    if isinstance(start_dt, str):
        start_dt = datetime.fromisoformat(start_dt)
    end_dt = start_dt + timedelta(minutes=candidate.duration_minutes)

    try:
        conflicts = find_conflicts(calendar, start_dt, end_dt)
    except Exception:
        logger.exception("Çakışma kontrolü başarısız (sohbet akışı)")
        return _finish_candidate(
            state, [translate("chat.calendar_error", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )

    if not conflicts:
        return _enter_preview(state, CONFLICT_NONE, lang=lang, prefix_messages=prefix_messages)

    try:
        alternatives = suggest_alternative_slots(calendar, candidate.duration_minutes, end_dt)
    except Exception:
        logger.exception("Alternatif saat önerisi başarısız (sohbet akışı)")
        alternatives = []

    if not alternatives:
        msg = translate(
            "chat.create.conflict_no_alternatives", lang,
            start=format_datetime(start_dt, lang), end=format_end_time_or_datetime(start_dt, end_dt, lang),
        )
        new_state = state.model_copy(update={"step": STEP_ASK_CONFLICT_NO_ALTERNATIVES, "attempts": 0})
        return new_state, prefix_messages + [msg]

    lines = [
        translate(
            "chat.create.conflict_found", lang,
            start=format_datetime(start_dt, lang), end=format_end_time_or_datetime(start_dt, end_dt, lang),
        ),
        translate("chat.create.alternatives_intro", lang),
    ]
    for i, alt in enumerate(alternatives, 1):
        lines.append(f"{i}. {format_datetime(alt, lang)}")
    new_state = state.model_copy(update={"step": STEP_ASK_CONFLICT_ALTERNATIVE, "alternatives": alternatives, "attempts": 0})
    return new_state, prefix_messages + lines


def _enter_preview(
    state: ChatState, conflict_token: str, *, lang: str, prefix_messages: list[str] | None = None
) -> tuple[ChatState, list[str]]:
    prefix_messages = prefix_messages or []
    # CLI: save_candidate yalnızca çakışma çözümü TAMAMLANDIĞINDA bir kez
    # çağrılıyor (review_and_confirm_candidate satır 616) — düzenleme
    # turlarında (candidate_saved zaten True) tekrar çağrılmaz. Kısa ömürlü
    # kendi bağlantısını açıyor (bkz. modül docstring'i) — burada hiçbir
    # LLM/takvim çağrısı olmadığından yazma anlık. original_snapshot da AYNI
    # noktada, yalnızca bir kez alınır (CLI satır 654 ile aynı yer) — ACM'nin
    # düzenleme-sonrası akışı (bkz. _finalize_create_event) buna ihtiyaç duyar.
    original_snapshot = state.original_snapshot
    if not state.candidate_saved:
        with get_connection() as conn:
            save_candidate(conn, state.candidate)
        original_snapshot = candidate_snapshot(state.candidate)
    new_state = state.model_copy(
        update={
            "step": STEP_PREVIEW_CONFIRM, "conflict_note": conflict_token, "candidate_saved": True,
            "attempts": 0, "pending_edit_field": None, "original_snapshot": original_snapshot,
        }
    )
    # Dosyadan çoklu-etkinlik çıkarımı (bkz. ChatState.queued_candidates):
    # batch_total > 1 iken önizlemenin önüne "Etkinlik 2/3" gibi bir ilerleme
    # satırı ekleniyor — kullanıcı tek tek onaylarken kaçıncı etkinlikte
    # olduğunu bilsin diye. Tekli akışlarda batch_total=0, hiçbir fark yok.
    preview_messages = prefix_messages
    if state.batch_total > 1:
        preview_messages = preview_messages + [
            translate("chat.create.batch_progress", lang, index=state.batch_index, total=state.batch_total)
        ]
    return new_state, preview_messages + [_render_preview(state.candidate, conflict_token, lang)]


def _reject_at_conflict(state: ChatState, *, embedding_provider, calendar, lang: str) -> tuple[ChatState, list[str]]:
    """Yalnızca İLK çakışma çözümlemesinde (candidate_saved henüz False'ken)
    ulaşılabilir — CLI'da `resolve_conflicts_interactively` alternatif
    gösterilip ne numara ne "d" seçilince `"Var (iptal edilecek)"` döner ve
    `review_and_confirm_candidate` bunu önizlemeye hiç girmeden reddediyor
    (satır 629, döngünün DIŞINDA). Düzenleme sonrası yeniden kontrollerde bu
    dala hiç girilmez (bkz. _advance_create_event: candidate_saved True ise
    bunun yerine CONFLICT_CANCELLED notuyla önizlemeye dönülür)."""
    with get_connection() as conn:
        save_candidate(conn, state.candidate)
        update_candidate_status(conn, state.candidate.candidate_id, CandidateStatus.REJECTED)
        record_audit(conn, "reject", state.candidate.candidate_id, "Çözülmeyen çakışma nedeniyle iptal (web sohbet).")
    return _finish_candidate(
        state, [translate("chat.create.cancelled_conflict", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
    )


def _finish_candidate(
    state: ChatState, messages: list[str], *, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    """create_event akışının (ve onu takip eden ACM alt-akışının) bir
    candidate için TAMAMEN bittiği HER noktada (onay/red/hata/vazgeç) bu
    çağrılır — dokuz ayrı `return ChatState(), [...]` yerine TEK bir çıkış
    noktası. Dosyadan çoklu etkinlik çıkarıldıysa (bkz.
    ChatState.queued_candidates, _dispatch_file_upload) sıradaki adayı
    otomatik başlatır; aksi halde normal idle duruma döner (mevcut davranış
    — queued_candidates hep boşsa hiçbir şey değişmez)."""
    if not state.queued_candidates:
        return ChatState(), messages

    next_candidate, *rest = state.queued_candidates
    applied_messages = apply_retrieved_policies(next_candidate, embedding_provider)
    next_state = ChatState(
        flow=FLOW_CREATE_EVENT,
        candidate=next_candidate,
        queued_candidates=rest,
        batch_index=state.batch_index + 1,
        batch_total=state.batch_total,
    )
    new_state, continue_messages = _continue_create_event(
        next_state, lang=lang, calendar=calendar, embedding_provider=embedding_provider, prefix_messages=applied_messages
    )
    return new_state, messages + continue_messages


def _finalize_create_event(
    state: ChatState, *, approved: bool, calendar, embedding_provider, lang: str
) -> tuple[ChatState, list[str]]:
    candidate = state.candidate
    if not approved:
        with get_connection() as conn:
            update_candidate_status(conn, candidate.candidate_id, CandidateStatus.REJECTED)
            record_audit(conn, "reject", candidate.candidate_id, "Kullanıcı reddetti (web sohbet).")
        # CLI: reddedilince review_and_confirm_candidate HEMEN ardından
        # capture_correction_interactively'yi çağırıyor (bkz. plan) — burada
        # bir sonraki turda cevaplanacak bir soruyla akışa devam ediyoruz.
        acm_state = state.model_copy(update={"step": STEP_ACM_ASK_REJECT_FEEDBACK})
        return acm_state, [
            translate("chat.create.rejected", lang),
            translate("chat.acm.ask_reject_feedback", lang),
        ]

    start_dt = candidate.start_datetime
    if isinstance(start_dt, str):
        start_dt = datetime.fromisoformat(start_dt)
    end_dt = start_dt + timedelta(minutes=candidate.duration_minutes or DEFAULT_MEETING_DURATION_MINUTES)

    # CLI'da (`review_and_confirm_candidate`) bu çağrı try/except'siz —
    # bir istisna traceback basıp CLI döngüsünü canlı bırakıyordu. Web'de
    # yakalanmazsa 500'e düşer ve oturumu kurtarılamaz bırakır; bu web
    # portunun CLI'dan BİLİNÇLİ bir iyileştirmesi (bkz. plan Riskler). Takvim
    # çağrısı herhangi bir SQLite bağlantısı AÇIKKEN yapılmıyor (bkz. modül
    # docstring'i) — yazma, ağ çağrısı BİTTİKTEN sonra kendi kısa bağlantısında.
    try:
        event_id = calendar.create_event(
            {
                "summary": candidate.title,
                "location": candidate.location,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": DEFAULT_TIMEZONE},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            }
        )
    except Exception:
        logger.exception("Takvime yazma başarısız (sohbet akışı)")
        return _finish_candidate(
            state, [translate("chat.calendar_error", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )

    with get_connection() as conn:
        update_candidate_status(conn, candidate.candidate_id, CandidateStatus.ADDED_TO_CALENDAR)
        set_candidate_google_event_id(conn, candidate.candidate_id, event_id)
        record_audit(conn, "approve_and_write", candidate.candidate_id, f"Google Calendar event_id={event_id} (web sohbet)")

    if not state.edited_structured_action:
        return _finish_candidate(
            state, [translate("chat.create.approved", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )

    # CLI: onaylanan candidate düzenlenmiş bir alan taşıyorsa (süre/önem),
    # takvime yazıldıktan HEMEN sonra capture_edit_correction çağrılıyor
    # (bkz. plan). Hangi alanın ne olduğu zaten kesin bilindiğinden (kullanıcı
    # direkt yazdı) burada LLM'e gerek yok — feedback metni deterministik
    # kuruluyor, save_user_correction kendi kısa bağlantısını açıyor.
    field_desc = ", ".join(
        f"{_STRUCTURED_ACTION_LABELS.get(k, k)} {v}" for k, v in state.edited_structured_action.items()
    )
    feedback = f"Kullanıcı önerideki alanı düzenledi: {field_desc}."
    correction = save_user_correction(candidate, feedback, original_output=state.original_snapshot)
    acm_state = state.model_copy(
        update={
            "step": STEP_ACM_ASK_APPLY_FUTURE,
            "acm_kind": ACM_KIND_EDIT,
            "acm_feedback": feedback,
            "acm_correction_id": correction.correction_id,
        }
    )
    return acm_state, [
        translate("chat.create.approved", lang),
        translate("chat.acm.ask_apply_future_edit", lang),
    ]


def _render_preview(candidate: CandidateEvent, conflict_token: str, lang: str) -> str:
    start = candidate.start_datetime
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    end = start + timedelta(minutes=candidate.duration_minutes) if start and candidate.duration_minutes else None

    time_text = "—"
    if start:
        time_text = format_datetime(start, lang)
        if end:
            time_text += f" – {format_end_time_or_datetime(start, end, lang)}"

    reminders_text = (
        ", ".join(translate("chat.create.reminder_item", lang, minutes=r.minutes_before) for r in candidate.reminders)
        if candidate.reminders
        else translate("common.unspecified", lang)
    )

    lines = [
        translate("chat.create.preview_header", lang) + ":",
        f"{translate('duzenle.field.title', lang)}: {candidate.title or translate('common.untitled', lang)}",
        f"{translate('oneriler.field.time', lang)}: {time_text}",
        f"{translate('duzenle.field.location', lang)}: {candidate.location or translate('common.unspecified', lang)}",
        f"{translate('oneriler.field.type', lang)}: {translate('enum.event_type.' + str(candidate.event_type), lang)}",
        f"{translate('oneriler.field.importance', lang)}: "
        f"{translate('enum.importance.' + str(candidate.importance), lang) if candidate.importance else translate('common.unspecified', lang)}",
        f"{translate('duzeltmelerim.field.reminders', lang)}: {reminders_text}",
        f"{translate('chat.create.conflict_label', lang)}: {translate('chat.create.conflict.' + conflict_token, lang)}",
    ]
    return "\n".join(lines)


def _match_edit_field(choice: str) -> str | None:
    for field, aliases in _EDIT_FIELD_ALIASES.items():
        if choice in aliases:
            return field
    return None


def _handle_edit_pick_field(state: ChatState, user_text: str, *, lang: str) -> tuple[ChatState, list[str]]:
    field = _match_edit_field(user_text.strip().lower())
    if field is None:
        new_state = state.model_copy(update={"step": STEP_PREVIEW_CONFIRM})
        return new_state, [
            translate("chat.create.edit_field_not_understood", lang),
            _render_preview(state.candidate, state.conflict_note, lang),
        ]
    new_state = state.model_copy(update={"step": STEP_EDIT_FIELD_VALUE, "pending_edit_field": field, "attempts": 0})
    return new_state, [translate(f"chat.create.edit_ask.{field}", lang)]


def _handle_edit_field_value(
    state: ChatState, user_text: str, *, calendar, embedding_provider, lang: str
) -> tuple[ChatState, list[str]]:
    candidate = state.candidate
    field = state.pending_edit_field
    raw = user_text.strip()

    if field == "title":
        candidate.title = raw
        return _after_edit(state, edited_category=None, calendar=calendar, embedding_provider=embedding_provider, lang=lang)

    if field == "location":
        candidate.location = raw or None
        return _after_edit(state, edited_category=None, calendar=calendar, embedding_provider=embedding_provider, lang=lang)

    if field == "start_datetime":
        try:
            new_value = ensure_timezone(raw or None)
            if new_value is None:
                raise ValueError("boş")
        except Exception:
            return _retry_edit_or_give_up(state, lang=lang, invalid_key="chat.create.start_datetime_invalid")
        candidate.start_datetime = new_value
        return _after_edit(state, edited_category=None, calendar=calendar, embedding_provider=embedding_provider, lang=lang)

    if field == "duration_minutes":
        minutes = parse_duration_minutes(raw)
        if not minutes:
            return _retry_edit_or_give_up(state, lang=lang, invalid_key="chat.create.duration_invalid")
        candidate.duration_minutes = minutes
        return _after_edit(
            state, edited_category="default_duration_minutes", calendar=calendar, embedding_provider=embedding_provider, lang=lang
        )

    if field == "importance":
        value = raw.lower()
        if value not in VALID_IMPORTANCE_VALUES:
            # CLI: geçersizse retry YOK, hiçbir şey değişmeden önizlemeye döner.
            new_state = state.model_copy(update={"step": STEP_PREVIEW_CONFIRM, "attempts": 0, "pending_edit_field": None})
            return new_state, [
                translate("chat.create.edit_importance_invalid", lang),
                _render_preview(candidate, state.conflict_note, lang),
            ]
        candidate.importance = value
        return _after_edit(state, edited_category="importance", calendar=calendar, embedding_provider=embedding_provider, lang=lang)

    new_state = state.model_copy(update={"step": STEP_PREVIEW_CONFIRM, "pending_edit_field": None})
    return new_state, [_render_preview(candidate, state.conflict_note, lang)]


def _retry_edit_or_give_up(state: ChatState, *, lang: str, invalid_key: str) -> tuple[ChatState, list[str]]:
    """saat/süre düzenlemesi CLI'da da bounded-retry'lı (MAX_CLARIFICATION_ATTEMPTS) —
    tükenince CLI sessizce vazgeçip önizlemeye döner (hiçbir şey değişmeden),
    burada da aynı davranış."""
    attempts = state.attempts + 1
    if attempts >= MAX_CLARIFICATION_ATTEMPTS:
        new_state = state.model_copy(update={"step": STEP_PREVIEW_CONFIRM, "attempts": 0, "pending_edit_field": None})
        return new_state, [_render_preview(state.candidate, state.conflict_note, lang)]
    new_state = state.model_copy(update={"attempts": attempts})
    return new_state, [translate(invalid_key, lang)]


def _after_edit(
    state: ChatState, *, edited_category: str | None, calendar, embedding_provider, lang: str
) -> tuple[ChatState, list[str]]:
    candidate = state.candidate
    edited_structured_action = dict(state.edited_structured_action)
    if edited_category:
        value = candidate.duration_minutes if edited_category == "default_duration_minutes" else candidate.importance
        edited_structured_action[edited_category] = value

    working_state = state.model_copy(
        update={"edited_structured_action": edited_structured_action, "pending_edit_field": None, "attempts": 0}
    )

    if candidate.start_datetime and candidate.duration_minutes:
        return _run_conflict_check(
            working_state, calendar=calendar, embedding_provider=embedding_provider, lang=lang, prefix_messages=[]
        )
    return _enter_preview(working_state, CONFLICT_NONE, lang=lang)


def _retry_or_give_up(
    state: ChatState, *, embedding_provider, calendar, lang: str, invalid_key: str
) -> tuple[ChatState, list[str]]:
    attempts = state.attempts + 1
    if attempts >= MAX_CLARIFICATION_ATTEMPTS:
        return _finish_candidate(
            state, [translate("chat.attempts_exhausted", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )
    new_state = state.model_copy(update={"attempts": attempts})
    return new_state, [translate(invalid_key, lang)]


# --- ACM "gelecekte de uygulayayım mı?" (bkz. capture_correction_interactively/
# capture_edit_correction, vertical_prototype.py) — reddetme ya da düzenleme-
# sonrası-onaydan sonra _finalize_create_event tarafından tetiklenir. ---


def _handle_acm_ask_reject_feedback(
    state: ChatState, user_text: str, *, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    feedback = user_text.strip()
    if not feedback or feedback.lower() in ("atla", "skip"):
        # CLI: boş Enter -> capture_correction_interactively sessizce return
        # eder, hiçbir düzeltme kaydedilmez. Web'de akışı net kapatmak için
        # kısa bir onay mesajı ekleniyor (CLI'dan bilinçli fark).
        return _finish_candidate(
            state, [translate("chat.acm.skipped", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )

    correction = save_user_correction(state.candidate, feedback)
    acm_state = state.model_copy(
        update={
            "step": STEP_ACM_ASK_APPLY_FUTURE, "acm_kind": ACM_KIND_REJECT,
            "acm_feedback": feedback, "acm_correction_id": correction.correction_id,
        }
    )
    return acm_state, [translate("chat.acm.ask_apply_future", lang)]


def _handle_acm_ask_apply_future(
    state: ChatState, user_text: str, *, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    if user_text.strip().lower() not in ("yes", "evet", "e"):
        return _finish_candidate(
            state, [translate("chat.acm.not_saved_as_rule", lang)], embedding_provider=embedding_provider, calendar=calendar, lang=lang
        )
    acm_state = state.model_copy(update={"step": STEP_ACM_ASK_SCOPE})
    event_type_label = translate("enum.event_type." + str(state.candidate.event_type), lang)
    return acm_state, [translate("chat.acm.ask_scope", lang, event_type=event_type_label)]


def _handle_acm_ask_scope(
    state: ChatState, user_text: str, *, llm, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    # CLI'nın _choose_scope_interactively'si (sender=None dalı) ile aynı
    # geri düşüş: yalnızca "2"/"her zaman" her-zaman kapsamı seçer, BAŞKA
    # HER GİRDİ (1 dahil) event_type-kapsamına düşer — geçersiz girdi için
    # ayrı bir retry yok, CLI'da da yok.
    choice = user_text.strip().lower()
    always = choice in ("2", "always", "her_zaman", "her zaman")

    if always:
        event_type_scope = None
        correction_scope = CorrectionScope.ACCOUNT
        scope_desc = translate("chat.acm.scope_always_desc", lang)
    else:
        event_type_scope = str(state.candidate.event_type)
        correction_scope = CorrectionScope.EVENT_TYPE
        scope_desc = translate(
            "chat.acm.scope_event_type_desc", lang,
            event_type=translate("enum.event_type." + event_type_scope, lang),
        )

    if state.acm_kind == ACM_KIND_EDIT:
        policy = save_derived_policy(
            embedding_provider, state.acm_feedback, state.edited_structured_action,
            event_type=event_type_scope, source=PolicySource.CORRECTION,
        )
    else:
        try:
            policy = derive_and_save_policy(
                llm, embedding_provider, state.acm_feedback,
                event_type=event_type_scope, infer_event_type=False, source=PolicySource.CORRECTION,
            )
        except JsonGenerationError:
            return _finish_candidate(
                state, [translate("chat.acm.extraction_failed", lang)],
                embedding_provider=embedding_provider, calendar=calendar, lang=lang,
            )
        if policy is None:
            return _finish_candidate(
                state, [translate("chat.acm.no_rule_extracted", lang)],
                embedding_provider=embedding_provider, calendar=calendar, lang=lang,
            )

    mark_correction_approved(state.acm_correction_id, policy.policy_id, correction_scope, None)
    return _finish_candidate(
        state, [translate("chat.acm.saved_policy", lang, scope=scope_desc, feedback=state.acm_feedback)],
        embedding_provider=embedding_provider, calendar=calendar, lang=lang,
    )


def _advance_create_event(
    state: ChatState, user_text: str, *, llm, embedding_provider, calendar, lang: str
) -> tuple[ChatState, list[str]]:
    candidate = state.candidate
    step = state.step

    if step == STEP_ASK_TITLE:
        title = user_text.strip()
        if not title:
            # CLI'dan bilinçli fark: boş başlığı sessizce kabul etmek yerine
            # yeniden sor (bkz. plan — kalıcı bir oturumda boş başlıklı bir
            # candidate'ın sessizce ilerlemesi CLI'dakinden daha kötü bir UX).
            return state, [translate("chat.create.ask_title", lang)]
        candidate.title = title
        candidate.missing_fields = [f for f in candidate.missing_fields if f != "title"]
        return _continue_create_event(state, lang=lang, calendar=calendar, embedding_provider=embedding_provider)

    if step == STEP_ASK_DURATION:
        minutes = parse_duration_minutes(user_text)
        if not minutes:
            return _retry_or_give_up(state, embedding_provider=embedding_provider, calendar=calendar, lang=lang, invalid_key="chat.create.duration_invalid")
        candidate.duration_minutes = minutes
        candidate.missing_fields = [f for f in candidate.missing_fields if f != "duration_minutes"]
        return _continue_create_event(state, lang=lang, calendar=calendar, embedding_provider=embedding_provider)

    if step == STEP_ASK_START_DATETIME:
        try:
            new_value = ensure_timezone(user_text.strip() or None)
            if new_value is None:
                raise ValueError("boş")
        except Exception:
            return _retry_or_give_up(state, embedding_provider=embedding_provider, calendar=calendar, lang=lang, invalid_key="chat.create.start_datetime_invalid")
        candidate.start_datetime = new_value
        candidate.missing_fields = [f for f in candidate.missing_fields if f != "start_datetime"]
        return _continue_create_event(state, lang=lang, calendar=calendar, embedding_provider=embedding_provider)

    if step == STEP_ASK_AMBIGUOUS_TIME:
        clock = parse_clock_time(user_text.strip())
        if not clock:
            return _retry_or_give_up(state, embedding_provider=embedding_provider, calendar=calendar, lang=lang, invalid_key="chat.create.ambiguous_time_invalid")
        start = candidate.start_datetime
        date_part = (
            start.date().isoformat() if isinstance(start, datetime) else (start or datetime.now().date().isoformat())[:10]
        )
        try:
            candidate.start_datetime = ensure_timezone(f"{date_part}T{clock}:00")
        except Exception:
            return _retry_or_give_up(state, embedding_provider=embedding_provider, calendar=calendar, lang=lang, invalid_key="chat.create.ambiguous_time_invalid")
        candidate.ambiguous_fields = [f for f in candidate.ambiguous_fields if f != "start_datetime"]
        return _continue_create_event(state, lang=lang, calendar=calendar, embedding_provider=embedding_provider)

    if step == STEP_ASK_CONFLICT_NO_ALTERNATIVES:
        token = CONFLICT_KEPT_ANYWAY if user_text.strip().lower() in ("yes", "evet", "e") else CONFLICT_UNRESOLVED
        return _enter_preview(state, token, lang=lang)

    if step == STEP_ASK_CONFLICT_ALTERNATIVE:
        choice = user_text.strip().lower()
        alts = state.alternatives
        if choice.isdigit() and 1 <= int(choice) <= len(alts):
            candidate.start_datetime = alts[int(choice) - 1]
            return _enter_preview(state, CONFLICT_MOVED, lang=lang)
        if choice in ("keep", "d"):
            return _enter_preview(state, CONFLICT_KEPT_ANYWAY, lang=lang)
        if not state.candidate_saved:
            return _reject_at_conflict(state, embedding_provider=embedding_provider, calendar=calendar, lang=lang)
        # Düzenleme sonrası yeniden kontrol — CLI'da bu durum OTOMATİK
        # REDDETMEZ (bkz. review_and_confirm_candidate satır 629'un while
        # döngüsünün DIŞINDA olması), yalnızca not olarak önizlemede gösterilir.
        return _enter_preview(state, CONFLICT_CANCELLED, lang=lang)

    if step == STEP_PREVIEW_CONFIRM:
        action = user_text.strip().lower()
        # "approve"/"edit"/"reject" buton value'ları (bkz. _asistan_chat.html);
        # e/h/d + evet/hayır/düzenle CLI'nın [e/h/d] kısayoluyla aynı —
        # serbest metinle yazanlar için de doğal olsun diye. CLI'dan bilinçli
        # fark: CLI'da "e" DIŞINDAKİ HER ŞEY reddediyordu (bkz. plan) — burada
        # yalnızca TANINAN eş anlamlılar bir aksiyona eşleniyor, tanınmayan
        # girdi önizlemeyi tekrar gösteriyor (kazara reddetmeyi önlemek için).
        if action in ("approve", "e", "evet", "onayla", "yes"):
            return _finalize_create_event(state, approved=True, calendar=calendar, embedding_provider=embedding_provider, lang=lang)
        if action in ("reject", "h", "hayır", "hayir", "reddet", "no"):
            return _finalize_create_event(state, approved=False, calendar=calendar, embedding_provider=embedding_provider, lang=lang)
        if action in ("edit", "d", "düzenle", "duzenle"):
            new_state = state.model_copy(update={"step": STEP_EDIT_PICK_FIELD})
            return new_state, [translate("chat.create.edit_pick_field_prompt", lang)]
        # Canlı testte bulundu: tanınmayan bir girdi (örn. saati düzeltmek için
        # doğrudan "19:00 olsun" yazmak) sessizce aynı önizlemeyi tekrar
        # gösteriyordu — kullanıcı mesajının hiçbir etkisi olmadığını
        # anlayamıyordu. Artık NEDEN hiçbir şey değişmediğini açıklayan bir
        # mesaj önce gösteriliyor (bkz. plan: kazara reddetmeyi önleme amacı
        # korunuyor, yalnızca sessizlik gideriliyor).
        return state, [translate("chat.create.preview_unrecognized", lang), _render_preview(candidate, state.conflict_note, lang)]

    if step == STEP_EDIT_PICK_FIELD:
        return _handle_edit_pick_field(state, user_text, lang=lang)

    if step == STEP_EDIT_FIELD_VALUE:
        return _handle_edit_field_value(state, user_text, calendar=calendar, embedding_provider=embedding_provider, lang=lang)

    if step == STEP_ACM_ASK_REJECT_FEEDBACK:
        return _handle_acm_ask_reject_feedback(state, user_text, embedding_provider=embedding_provider, calendar=calendar, lang=lang)

    if step == STEP_ACM_ASK_APPLY_FUTURE:
        return _handle_acm_ask_apply_future(state, user_text, embedding_provider=embedding_provider, calendar=calendar, lang=lang)

    if step == STEP_ACM_ASK_SCOPE:
        return _handle_acm_ask_scope(state, user_text, llm=llm, embedding_provider=embedding_provider, calendar=calendar, lang=lang)

    logger.warning("Bilinmeyen create_event step: %r — oturum sıfırlanıyor", step)
    return ChatState(), [translate("chat.generic_error", lang)]


# --- update_event ---


def _event_title(event: dict, lang: str) -> str:
    return event.get("summary") or translate("common.untitled", lang)


def _event_start_raw(event: dict) -> str | None:
    return event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")


def _format_event_line(event: dict, lang: str) -> str:
    entry = parse_google_event(event, DEFAULT_TIMEZONE)
    if entry is None:
        return _event_title(event, lang)
    time_text = translate("chat.query.all_day", lang) if entry.all_day else format_datetime(entry.start, lang)
    return f"{entry.title or translate('common.untitled', lang)} ({time_text})"


def _resolve_update_duration(fields: dict, event: dict) -> int:
    duration = fields.get("new_duration_minutes")
    if duration:
        return int(duration)
    old_start_raw = _event_start_raw(event)
    old_end_raw = event.get("end", {}).get("dateTime")
    if old_start_raw and old_end_raw:
        try:
            return int(
                (datetime.fromisoformat(old_end_raw) - datetime.fromisoformat(old_start_raw)).total_seconds() / 60
            )
        except ValueError:
            pass
    return DEFAULT_MEETING_DURATION_MINUTES


def _start_update_event(user_text: str, *, llm, calendar, lang: str) -> tuple[ChatState, list[str]]:
    today = datetime.now().astimezone()
    try:
        fields = generate_json(llm, _update_event_extraction_system_prompt(today), user_text)
    except JsonGenerationError:
        return ChatState(), [translate("chat.update.extraction_failed", lang)]

    try:
        candidates = _find_matching_events(calendar, fields.get("title_hint"), fields.get("date_hint"))
    except Exception:
        logger.exception("Etkinlik arama başarısız (sohbet akışı)")
        return ChatState(), [translate("chat.calendar_error", lang)]

    if not candidates:
        return ChatState(), [translate("chat.update.no_matches", lang)]

    if len(candidates) > 1:
        shortlist = candidates[:5]
        lines = [translate("chat.update.disambiguate_prompt", lang)]
        for i, e in enumerate(shortlist, 1):
            lines.append(f"{i}. {_format_event_line(e, lang)}")
        state = ChatState(flow=FLOW_UPDATE_EVENT, step=STEP_UPDATE_DISAMBIGUATE, update_fields=fields, shortlist=shortlist)
        return state, lines

    return _proceed_with_matched_event(candidates[0], fields, calendar=calendar, lang=lang)


def _proceed_with_matched_event(event: dict, fields: dict, *, calendar, lang: str) -> tuple[ChatState, list[str]]:
    if fields.get("cancel"):
        state = ChatState(flow=FLOW_UPDATE_EVENT, step=STEP_UPDATE_CONFIRM_DELETE, update_fields=fields, matched_event=event)
        msg = translate(
            "chat.update.confirm_delete", lang, title=_event_title(event, lang), when=_format_event_line(event, lang)
        )
        return state, [msg]

    new_start_raw = fields.get("new_start_datetime")
    if not new_start_raw:
        return ChatState(), [translate("chat.update.no_new_time", lang)]
    try:
        new_start = ensure_timezone(new_start_raw)
        if new_start is None:
            raise ValueError("boş")
    except Exception:
        return ChatState(), [translate("chat.update.no_new_time", lang)]

    duration = _resolve_update_duration(fields, event)
    new_end = new_start + timedelta(minutes=duration)

    old_start_raw = _event_start_raw(event)
    try:
        conflicts = find_conflicts(calendar, new_start, new_end)
    except Exception:
        logger.exception("Çakışma kontrolü başarısız (update_event, sohbet akışı)")
        conflicts = []
    # Etkinliğin kendi eski zamanı yeni aralıkla örtüşüyorsa (örn. süre
    # uzatılırken) freebusy'de "meşgul" görünebilir — kendisiyle çakışma
    # sayılmasın diye eski başlangıcıyla tam örtüşen sonuç göz ardı edilir
    # (bkz. CLI'nın handle_update_event'i, aynı mantık).
    if old_start_raw:
        conflicts = [c for c in conflicts if c[0].isoformat() != old_start_raw]

    state = ChatState(flow=FLOW_UPDATE_EVENT, step=STEP_UPDATE_CONFIRM_MOVE, update_fields=fields, matched_event=event)
    lines = [
        translate(
            "chat.update.confirm_move", lang,
            title=_event_title(event, lang), old=_format_event_line(event, lang), new=format_datetime(new_start, lang),
        )
    ]
    if conflicts:
        lines.append(translate("chat.update.move_conflict_warning", lang, count=len(conflicts)))
    return state, lines


def _advance_update_event(state: ChatState, user_text: str, *, calendar, lang: str) -> tuple[ChatState, list[str]]:
    step = state.step

    if step == STEP_UPDATE_DISAMBIGUATE:
        choice = user_text.strip().lower()
        shortlist = state.shortlist or []
        if not choice.isdigit() or not (1 <= int(choice) <= len(shortlist)):
            # CLI: geçersiz seçim retry'siz doğrudan iptal (bkz. handle_update_event).
            return ChatState(), [translate("chat.update.cancelled", lang)]
        event = shortlist[int(choice) - 1]
        return _proceed_with_matched_event(event, state.update_fields, calendar=calendar, lang=lang)

    if step == STEP_UPDATE_CONFIRM_DELETE:
        if user_text.strip().lower() in ("yes", "evet", "e"):
            event = state.matched_event
            try:
                calendar.delete_event(event["id"])
            except Exception:
                logger.exception("Etkinlik silme başarısız (sohbet akışı)")
                return ChatState(), [translate("chat.calendar_error", lang)]
            with get_connection() as conn:
                record_audit(
                    conn, "delete_event", event["id"], "Kullanıcı isteğiyle silindi (web sohbet).",
                    entity_type="calendar_event",
                )
            return ChatState(), [translate("chat.update.deleted", lang)]
        return ChatState(), [translate("chat.update.cancelled", lang)]

    if step == STEP_UPDATE_CONFIRM_MOVE:
        if user_text.strip().lower() in ("yes", "evet", "e"):
            event = state.matched_event
            fields = state.update_fields
            new_start = ensure_timezone(fields.get("new_start_datetime"))
            duration = _resolve_update_duration(fields, event)
            new_end = new_start + timedelta(minutes=duration)
            try:
                calendar.update_event(
                    event["id"],
                    {
                        "start": {"dateTime": new_start.isoformat(), "timeZone": DEFAULT_TIMEZONE},
                        "end": {"dateTime": new_end.isoformat(), "timeZone": DEFAULT_TIMEZONE},
                    },
                )
            except Exception:
                logger.exception("Etkinlik güncelleme başarısız (sohbet akışı)")
                return ChatState(), [translate("chat.calendar_error", lang)]
            with get_connection() as conn:
                record_audit(
                    conn, "update_event", event["id"], "Kullanıcı isteğiyle güncellendi (web sohbet).",
                    entity_type="calendar_event",
                )
            return ChatState(), [translate("chat.update.updated", lang)]
        return ChatState(), [translate("chat.update.cancelled", lang)]

    logger.warning("Bilinmeyen update_event step: %r — oturum sıfırlanıyor", step)
    return ChatState(), [translate("chat.generic_error", lang)]
