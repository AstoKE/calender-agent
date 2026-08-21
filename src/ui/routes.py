"""Web UI route'ları: tam sol navigasyon + yedi ekran (bkz. docs/architecture-plan.md §16).

Sade HTML form + POST + redirect (MPA) — bu dilimde çok az JS var (yalnızca
base.html'deki menü kapatma satır-içi script'i). İş mantığı burada minimal
tutuluyor: gerçek CRUD ilgili store modüllerinde, çakışma kontrolü
src/services/availability.py'de, tarama src/services/scan_inbox.py'de
(hepsi CLI ile paylaşılıyor).

Route YOLLARI kasıtlı olarak Türkçe kalıyor (/oneriler, /hesaplar, ...) —
yalnızca şablonlardaki görünen ETİKETLER çevriliyor (bkz. src/localization).
Dil başına ayrı route (örn. /suggestions) bookmark/geri tuşu/`next=`
parametresini dile bağımlı yapardı; tek kullanıcılı localhost bir uygulamada
URL kullanıcıya görünen metin değil, o yüzden bu maliyete değmiyor.

Eskiden burada bir `/{page_name}` catch-all'ı vardı (henüz gerçek içeriği
olmayan ekranlar için "yakında" placeholder'ı) — 7 ekranın hepsi gerçek
içerik alınca kaldırıldı (bkz. app.py'deki 404 exception handler). O
catch-all'ın "dosyanın en sonunda kalmalı" kısıtı da onunla birlikte
ortadan kalktı; yeni route'lar dosyada istenilen yere eklenebilir."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.candidates.store import (
    count_pending_candidates,
    get_pending_candidate,
    list_pending_candidates,
    record_candidate_audit,
    update_candidate_fields,
    update_candidate_status,
)
from src.connectors.account_registry import list_accounts
from src.connectors.google_calendar import GoogleCalendarConnector
from src.core.logging_config import LOG_PATH, get_logger
from src.core.models import CandidateStatus, PolicySource
from src.localization import normalize_language
from src.localization.preferences import get_effective_timezone, get_localization_preference, set_timezone, set_ui_language
from src.memory.correction_memory import (
    count_corrections,
    delete_correction,
    list_corrections,
    save_user_correction,
    set_correction_future_use,
)
from src.policies.derivation import derive_and_save_policy, save_derived_policy
from src.policies.store import deactivate_policy_by_id, get_active_policies, list_policies, reactivate_policy
from src.providers.json_generation import JsonGenerationError
from src.services.availability import find_conflicts
from src.services.calendar_view import (
    SLOT_MINUTES,
    adjacent_month_anchor,
    day_bounds,
    default_scroll_row,
    group_by_day,
    month_grid,
    parse_google_event,
    week_bounds,
)
from src.services.scan_inbox import scan_account_inbox
from src.services.timeutil import DEFAULT_TIMEZONE
from src.storage.db import DEFAULT_DB_PATH
from src.storage.preferences import set_preference
from src.ui.calendar_access import get_calendar_or_none
from src.ui.chat_state import build_chat_widget_context
from src.ui.session import VALID_THEMES, resolve_active_account, safe_next, set_session_cookies
from src.ui.templating import templates

router = APIRouter()
logger = get_logger("ui.routes")

# Faz 5 (bkz. plan "Web Chatbox"): burada tanımlı, chat_routes.py'de DEĞİL —
# chat_routes.py zaten bu modülden _get_calendar'ı içe aktarıyor, tersi yönde
# bir bağımlılık (routes.py -> chat_routes.py) döngüsel import'a yol açardı.
CHAT_ENABLED = True

CURATED_TIMEZONES = [
    "Europe/Istanbul",
    "Europe/London",
    "Europe/Berlin",
    "UTC",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Dubai",
    "Asia/Tokyo",
]


def _get_calendar(request: Request, account_id: str) -> GoogleCalendarConnector:
    cache = request.app.state.calendar_connectors
    if account_id not in cache:
        cache[account_id] = GoogleCalendarConnector(account_id=account_id)
    return cache[account_id]


@router.get("/")
def root():
    return RedirectResponse("/anasayfa")


@router.get("/anasayfa", response_class=HTMLResponse)
def home_page(request: Request, mesgul: bool = False):
    active_account = resolve_active_account(request)

    today_entries: list = []
    calendar_state = "no_account" if active_account is None else "ok"

    if active_account is not None:
        calendar = get_calendar_or_none(request, active_account["id"])
        if calendar is None:
            calendar_state = "unavailable"
        else:
            try:
                tz_name = get_effective_timezone(active_account["id"])
                time_min, time_max = day_bounds(date.today(), tz_name)
                raw_events = calendar.list_events(time_min, time_max)
                today_entries = [
                    entry for raw in raw_events if (entry := parse_google_event(raw, tz_name)) is not None
                ]
                today_entries.sort(key=lambda e: e.start)
            except Exception as exc:
                logger.warning(
                    "Ana Sayfa: bugünkü etkinlikler yüklenemedi (account=%s): %s",
                    active_account["id"],
                    exc,
                )
                calendar_state = "unavailable"

    account_id = active_account["id"] if active_account else None
    pending_preview = list_pending_candidates(account_id)[:3] if account_id else []
    pending_count = count_pending_candidates(account_id) if account_id else 0
    others_pending = (count_pending_candidates(None) - pending_count) if account_id else 0

    chat_context = build_chat_widget_context(request, account_id) if CHAT_ENABLED else {}

    return templates.TemplateResponse(
        request,
        "anasayfa.html",
        {
            "active_page": "anasayfa",
            "calendar_state": calendar_state,
            "today_entries": today_entries,
            "pending_preview": pending_preview,
            "pending_count": pending_count,
            "others_pending": others_pending,
            "active_policy_count": len(get_active_policies()),
            "scan_busy": mesgul,
            "chat_enabled": CHAT_ENABLED,
            **chat_context,
        },
    )


@router.post("/dil")
def set_language(request: Request, dil: str = Form(...), next: str = Form("/anasayfa")):
    lang = normalize_language(dil)
    active_account = resolve_active_account(request)
    if active_account:
        set_ui_language(active_account["id"], lang)
    else:
        # localization_preferences.account_id hesaba FK verdiği için hiç
        # hesap yokken oraya yazılamaz — user_preferences (FK'sız) kalıcı
        # geri düşüş katmanı (bkz. src/ui/session.py::resolve_language).
        set_preference("ui.language", lang)
    response = RedirectResponse(safe_next(next), status_code=303)
    set_session_cookies(response, lang=lang)
    return response


@router.post("/tema")
def set_theme(tema: str = Form(...), next: str = Form("/anasayfa")):
    theme = tema if tema in VALID_THEMES else "system"
    response = RedirectResponse(safe_next(next), status_code=303)
    set_session_cookies(response, theme=theme)
    return response


@router.post("/hesap-sec")
def select_account(account_id: str = Form(...), next: str = Form("/anasayfa")):
    valid_ids = {acc["id"] for acc in list_accounts()}
    response = RedirectResponse(safe_next(next), status_code=303)
    if account_id in valid_ids:
        set_session_cookies(response, account_id=account_id)
    return response


@router.get("/hesaplar", response_class=HTMLResponse)
def accounts_page(
    request: Request,
    tarandi: int | None = None,
    bulunan: int | None = None,
    hatali: int | None = None,
    mesgul: bool = False,
    hesap_eklendi: bool = False,
    oauth_hata: str | None = None,
):
    accounts = list_accounts()
    scan_result = None
    if tarandi is not None:
        scan_result = {"total": tarandi, "candidates_found": bulunan or 0, "skipped_errors": hatali or 0}
    return templates.TemplateResponse(
        request,
        "hesaplar.html",
        {
            "accounts": accounts,
            "scan_result": scan_result,
            "scan_busy": mesgul,
            "active_page": "hesaplar",
            "account_added": hesap_eklendi,
            "oauth_error": oauth_hata,
        },
    )


def _run_scan(request: Request, account_id: str, redirect_base: str) -> RedirectResponse:
    """`/hesaplar/{id}/tara` (belirli hesap) ve `/tara` (aktif hesap, Ana
    Sayfa) tarafından paylaşılıyor. Tarama dakikalarca blokluyor — aynı
    hesap için ikinci bir tarama zaten sürüyorsa yeniden BAŞLATMAZ, sadece
    "meşgul" bilgisiyle geri döner (bkz. app.py::lifespan scan_in_progress)."""
    in_progress = request.app.state.scan_in_progress
    if account_id in in_progress:
        return RedirectResponse(f"{redirect_base}?mesgul=1", status_code=303)
    in_progress.add(account_id)
    try:
        result = scan_account_inbox(account_id, request.app.state.llm, request.app.state.embedding_provider)
    finally:
        in_progress.discard(account_id)
    return RedirectResponse(
        f"{redirect_base}?tarandi={result['total']}&bulunan={result['candidates_found']}&hatali={result['skipped_errors']}",
        status_code=303,
    )


@router.post("/hesaplar/{account_id}/tara")
def scan_account(request: Request, account_id: str):
    return _run_scan(request, account_id, "/hesaplar")


@router.post("/tara")
def scan_active_account(request: Request):
    active_account = resolve_active_account(request)
    if active_account is None:
        return RedirectResponse("/anasayfa", status_code=303)
    return _run_scan(request, active_account["id"], "/anasayfa")


@router.get("/takvim", response_class=HTMLResponse)
def calendar_page(request: Request, hafta: str | None = None):
    active_account = resolve_active_account(request)
    if active_account is None:
        return templates.TemplateResponse(
            request, "takvim.html", {"active_page": "takvim", "state": "no_account"}
        )

    try:
        anchor = date.fromisoformat(hafta) if hafta else date.today()
    except ValueError:
        anchor = date.today()

    tz_name = get_effective_timezone(active_account["id"])
    calendar = get_calendar_or_none(request, active_account["id"])
    if calendar is None:
        return templates.TemplateResponse(
            request, "takvim.html", {"active_page": "takvim", "state": "no_token"}
        )

    time_min, time_max = week_bounds(anchor, tz_name)
    try:
        raw_events = calendar.list_events(time_min, time_max)
    except Exception as exc:
        logger.warning("Takvim yüklenemedi (account=%s): %s", active_account["id"], exc)
        return templates.TemplateResponse(
            request,
            "takvim.html",
            {"active_page": "takvim", "state": "error", "error_detail": str(exc)},
        )

    entries = [entry for raw in raw_events if (entry := parse_google_event(raw, tz_name)) is not None]
    buckets = group_by_day(entries, time_min.date(), 7)

    # "Şu an" çizgisi (Google Calendar'daki kırmızı çizgi benzeri, bkz.
    # takvim.html) yalnızca bugün görüntülenen haftadaysa anlamlı — SLOT_MINUTES
    # dakikalık grid'e göre saat-ızgarasındaki satır konumu hesaplanır.
    now = datetime.now(ZoneInfo(tz_name))
    now_row = (now.hour * 60 + now.minute) // SLOT_MINUTES + 1

    return templates.TemplateResponse(
        request,
        "takvim.html",
        {
            "active_page": "takvim",
            "state": "ok",
            "buckets": buckets,
            "today": date.today(),
            "today_iso": date.today().isoformat(),
            "prev_week": (anchor - timedelta(days=7)).isoformat(),
            "next_week": (anchor + timedelta(days=7)).isoformat(),
            "now_row": now_row,
            "scroll_to_row": default_scroll_row(buckets),
            "anchor": anchor,
            "anchor_iso": anchor.isoformat(),
            "mini_calendar": month_grid(anchor),
            "mini_prev_month": adjacent_month_anchor(anchor, -1).isoformat(),
            "mini_next_month": adjacent_month_anchor(anchor, 1).isoformat(),
        },
    )


@router.get("/kurallarim", response_class=HTMLResponse)
def rules_page(request: Request, cakisma: bool = False):
    policies = list_policies(include_inactive=True)
    return templates.TemplateResponse(
        request,
        "kurallarim.html",
        {
            "active_page": "kurallarim",
            "active_policies": [p for p in policies if p.active],
            "inactive_policies": [p for p in policies if not p.active],
            "reactivate_conflict": cakisma,
        },
    )


@router.post("/kurallarim/{policy_id}/pasiflestir")
def deactivate_rule(policy_id: str):
    deactivate_policy_by_id(policy_id)
    return RedirectResponse("/kurallarim", status_code=303)


@router.post("/kurallarim/{policy_id}/aktiflestir")
def reactivate_rule(policy_id: str):
    if reactivate_policy(policy_id) is None:
        return RedirectResponse("/kurallarim?cakisma=1", status_code=303)
    return RedirectResponse("/kurallarim", status_code=303)


@router.get("/kurallarim/yeni", response_class=HTMLResponse)
def new_rule_form(request: Request, hata: str | None = None):
    return templates.TemplateResponse(
        request, "kural_yeni.html", {"active_page": "kurallarim", "error_kind": hata}
    )


@router.post("/kurallarim/yeni")
def new_rule_submit(
    request: Request,
    natural_language_rule: str = Form(...),
    scope_type: str = Form("global"),
    event_type: str = Form(""),
    sender: str = Form(""),
    action_type: str = Form(...),
    duration_minutes: str = Form(""),
    reminder_minutes: str = Form(""),
    importance: str = Form(""),
):
    structured_action: dict = {}
    if action_type == "duration" and duration_minutes.strip():
        structured_action["default_duration_minutes"] = int(duration_minutes)
    elif action_type == "reminder" and reminder_minutes.strip():
        structured_action["reminder_minutes_before"] = int(reminder_minutes)
    elif action_type == "importance" and importance.strip():
        structured_action["importance"] = importance

    if not structured_action:
        return RedirectResponse("/kurallarim/yeni?hata=eksik", status_code=303)

    save_derived_policy(
        request.app.state.embedding_provider,
        natural_language_rule,
        structured_action,
        event_type=(event_type or None) if scope_type == "event_type" else None,
        sender=(sender or None) if scope_type == "sender" else None,
        source=PolicySource.MANUAL,
    )
    return RedirectResponse("/kurallarim", status_code=303)


@router.post("/kurallarim/yeni-dogal-dil")
def new_rule_submit_natural_language(request: Request, rule_text: str = Form(...)):
    """Mode B (bkz. plan Faz 10): kullanıcı yapılandırılmış alanları
    doldurmak yerine tek bir doğal dil cümlesi yazar, LLM'in çıkardığı
    structured_action ile derive_and_save_policy kaydeder. Yapılandırılmış
    moddan (new_rule_submit) FARKLI: LLM çağrısı birkaç saniye sürebilir
    ve şablonda ("kural_yeni.natural.hint") bu açıkça belirtiliyor."""
    try:
        policy = derive_and_save_policy(
            request.app.state.llm,
            request.app.state.embedding_provider,
            rule_text,
            source=PolicySource.MANUAL,
        )
    except JsonGenerationError:
        policy = None

    if policy is None:
        return RedirectResponse("/kurallarim/yeni?hata=llm", status_code=303)
    return RedirectResponse("/kurallarim", status_code=303)


@router.get("/duzeltmelerim", response_class=HTMLResponse)
def corrections_page(request: Request, tur: str | None = None):
    filter_type = "field" if tur == "alan" else "classification" if tur == "siniflandirma" else None
    return templates.TemplateResponse(
        request,
        "duzeltmelerim.html",
        {
            "active_page": "duzeltmelerim",
            "corrections": list_corrections(correction_type=filter_type),
            "current_filter": tur or "hepsi",
            "total_count": count_corrections(),
            "field_count": count_corrections("field"),
            "classification_count": count_corrections("classification"),
        },
    )


@router.post("/duzeltmelerim/{correction_id}/gelecekte-kullan")
def toggle_correction_future_use(correction_id: str, enabled: bool = Form(...)):
    set_correction_future_use(correction_id, enabled)
    return RedirectResponse("/duzeltmelerim", status_code=303)


@router.post("/duzeltmelerim/{correction_id}/sil")
def delete_correction_route(correction_id: str):
    delete_correction(correction_id)
    return RedirectResponse("/duzeltmelerim", status_code=303)


@router.get("/ayarlar", response_class=HTMLResponse)
def settings_page(request: Request):
    active_account = resolve_active_account(request)
    current_timezone = DEFAULT_TIMEZONE
    if active_account:
        pref = get_localization_preference(active_account["id"])
        if pref and pref.get("timezone"):
            current_timezone = pref["timezone"]

    return templates.TemplateResponse(
        request,
        "ayarlar.html",
        {
            "active_page": "ayarlar",
            "current_timezone": current_timezone,
            "timezones": CURATED_TIMEZONES,
            "db_path": str(DEFAULT_DB_PATH),
            "log_path": str(LOG_PATH),
            "chat_model": "qwen3-4b",
            "embedding_model": "qwen3-embedding-0.6b",
        },
    )


@router.post("/ayarlar/bolge")
def set_region_settings(request: Request, saat_dilimi: str = Form(...)):
    active_account = resolve_active_account(request)
    if active_account:
        set_timezone(active_account["id"], saat_dilimi)
    return RedirectResponse("/ayarlar", status_code=303)


@router.get("/oneriler", response_class=HTMLResponse)
def list_candidates(request: Request):
    pending = list_pending_candidates()
    return templates.TemplateResponse(
        request, "oneriler.html", {"pending": pending, "active_page": "oneriler"}
    )


@router.post("/oneriler/{candidate_id}/onayla")
def approve(request: Request, candidate_id: str, force: bool = Form(False), next: str = Form("/oneriler")):
    next_url = safe_next(next, fallback="/oneriler")
    pending = get_pending_candidate(candidate_id)
    if pending is None:
        return RedirectResponse(next_url, status_code=303)
    candidate = pending["candidate"]

    if candidate.missing_fields or candidate.ambiguous_fields:
        return RedirectResponse(
            f"/oneriler/{candidate_id}/duzenle?next={quote(next_url, safe='')}", status_code=303
        )

    calendar = _get_calendar(request, pending["account_id"])
    start_dt = candidate.start_datetime
    end_dt = start_dt + timedelta(minutes=candidate.duration_minutes)

    if not force:
        conflicts = find_conflicts(calendar, start_dt, end_dt)
        if conflicts:
            return templates.TemplateResponse(
                request,
                "cakisma_onay.html",
                {"pending": pending, "conflicts": conflicts, "active_page": "oneriler", "next_url": next_url},
            )

    event_id = calendar.create_event(
        {
            "summary": candidate.title,
            "location": candidate.location,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": DEFAULT_TIMEZONE},
        }
    )
    update_candidate_status(candidate_id, CandidateStatus.ADDED_TO_CALENDAR)
    record_candidate_audit("approve_and_write", candidate_id, f"Web'den onaylandı, event_id={event_id}")
    return RedirectResponse(next_url, status_code=303)


@router.post("/oneriler/{candidate_id}/reddet")
def reject(candidate_id: str, reason: str = Form(""), next: str = Form("/oneriler")):
    next_url = safe_next(next, fallback="/oneriler")
    # Sırayla dikkat: önce candidate'ı (hâlâ NEEDS_INFORMATION/READY_FOR_CONFIRMATION
    # durumundayken) çekiyoruz — status REJECTED'a çevrildikten sonra
    # get_pending_candidate onu artık bulamaz (WHERE status IN (...) filtresi).
    pending = get_pending_candidate(candidate_id)
    if pending is None:
        return RedirectResponse(next_url, status_code=303)

    update_candidate_status(candidate_id, CandidateStatus.REJECTED)
    record_candidate_audit(
        "reject", candidate_id, reason.strip() or "Web'den reddedildi (sebep belirtilmedi)."
    )
    # ACM'nin "gelecekte de uygulayayım mı?" akışı (politika türetme, scope
    # seçimi) bu dilimde YOK — yalnızca ham düzeltme kaydediliyor, denetim/
    # ileride manuel inceleme için. Politika teklifi CLI'da kalmaya devam ediyor.
    if reason.strip():
        save_user_correction(pending["candidate"], reason.strip())
    return RedirectResponse(next_url, status_code=303)


@router.get("/oneriler/{candidate_id}/duzenle", response_class=HTMLResponse)
def edit_form(request: Request, candidate_id: str, next: str = "/oneriler"):
    next_url = safe_next(next, fallback="/oneriler")
    pending = get_pending_candidate(candidate_id)
    if pending is None:
        return RedirectResponse(next_url, status_code=303)
    return templates.TemplateResponse(
        request, "duzenle.html", {"pending": pending, "active_page": "oneriler", "next_url": next_url}
    )


@router.post("/oneriler/{candidate_id}/duzenle")
def edit_submit(
    candidate_id: str,
    title: str = Form(""),
    start_datetime: str = Form(""),
    duration_minutes: str = Form(""),
    importance: str = Form(""),
    location: str = Form(""),
    next: str = Form("/oneriler"),
):
    try:
        update_candidate_fields(
            candidate_id,
            title=title or None,
            start_datetime=start_datetime or None,
            duration_minutes=duration_minutes or None,
            importance=importance or None,
            location=location or None,
        )
    except ValueError:
        # Candidate zaten silinmiş/durumu değişmiş (çift gönderim, geri tuşu) —
        # 500 yerine listeye dön, artık orada görünmeyecek zaten.
        pass
    return RedirectResponse(safe_next(next, fallback="/oneriler"), status_code=303)
