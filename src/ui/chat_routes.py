"""Sohbet rotaları: `POST /asistan/mesaj`, `POST /asistan/yeni-sohbet`,
`GET /asistan/gecmis`, `POST /asistan/sohbete-don/{session_id}` (bkz. plan
"Web Chatbox" Faz 5 + "Geçmiş Sohbetler"). Ayrı dosya — `routes.py` zaten
500+ satır.

`_get_calendar` (interaktif-OAuth-toleranslı) `routes.py`'den içe aktarılıyor,
`get_calendar_or_none` DEĞİL: bir sohbet mesajı göndermek `/oneriler/{id}/onayla`
gibi açık bir kullanıcı eylemi, Takvim'in otomatik sayfa-yükleme widget'ı gibi
değil (bkz. plan Mimari kararlar).

İki yanıt biçimi destekleniyor (bkz. templates/anasayfa.html'deki gönderim
script'i): normal form gönderimi (JS yok/başarısız) 303 redirect alır — tam
sayfa yeniden yüklenir, MPA'nın doğal davranışı. `X-Requested-With: fetch`
başlığıyla gelen istek (bizim kendi script'imiz) bunun yerine YALNIZCA
`_asistan_chat.html` fragment'ının yeniden render edilmiş HTML'ini alır —
sayfa hiç yenilenmez, yalnızca sohbet widget'ı güncellenir. Aynı Jinja
şablonu iki yoldan da kullanıldığı için görüntü hiç ayrışmıyor (bkz.
build_chat_widget_context, src/ui/chat_state.py)."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from src.connectors.account_registry import resolve_write_account_id
from src.core.logging_config import get_logger
from src.localization import translate
from src.providers.base import FileInputCapable
from src.ui.chat_session import (
    create_new_chat_session,
    get_or_create_chat_session,
    list_chat_sessions,
    switch_chat_session,
)
from src.ui.chat_state import append_chat_message, build_chat_widget_context, process_message, widget_context_for_session
from src.ui.routes import CHAT_ENABLED, _get_calendar
from src.ui.session import resolve_active_account, resolve_language, safe_next
from src.ui.templating import templates
from src.storage.db import get_connection

router = APIRouter()
logger = get_logger("ui.chat_routes")


def _is_ajax(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def _chat_fragment_response(request: Request, account_id: str | None):
    return templates.TemplateResponse(
        request, "partials/_asistan_chat.html", build_chat_widget_context(request, account_id)
    )


def _chat_fragment_response_for_session(request: Request, session_id: str | None):
    vision_enabled = isinstance(getattr(request.app.state, "llm", None), FileInputCapable)
    return templates.TemplateResponse(
        request,
        "partials/_asistan_chat.html",
        widget_context_for_session(session_id, vision_enabled=vision_enabled),
    )


def _copy_cookies(source: Response, target: Response) -> None:
    for key, value in source.raw_headers:
        if key == b"set-cookie":
            target.raw_headers.append((key, value))


# Hızlı-yanıt butonlarının (bkz. partials/_asistan_chat.html) ham action=
# değeri, aynı butonun kendi görünen etiketiyle eşleniyor — kullanıcı balonu
# "approve" gibi ham bir İngilizce token yerine "Onayla" gösterir (canlı
# testte bulunan bir kusur). Yalnızca adımdan BAĞIMSIZ, tek anlamlı token'lar
# burada — "1"/"2" gibi adıma göre anlamı değişenler kasıtlı olarak dışarıda,
# yanlış çeviri göstermektense ham sayıyı göstermek daha az yanıltıcı.
_ACTION_DISPLAY_KEYS = {
    "approve": "common.approve",
    "edit": "common.edit",
    "reject": "common.reject",
    "evet": "chat.yes",
    "hayır": "chat.no",
    "keep": "chat.create.keep_anyway",
    "atla": "chat.acm.skip",
    "title": "duzenle.field.title",
    "start_datetime": "oneriler.field.time",
    "duration_minutes": "duzenle.field.duration",
    "importance": "duzenle.field.importance",
    "location": "duzenle.field.location",
}


def _display_text_for_action(action: str, metin: str, lang: str) -> str | None:
    if metin.strip():
        return None  # serbest metin -> kullanıcının kendi yazdığı, dokunulmaz
    key = _ACTION_DISPLAY_KEYS.get(action.strip().lower())
    return translate(key, lang) if key else None


@router.post("/asistan/mesaj")
def send_chat_message(
    request: Request,
    metin: str = Form(""),
    action: str = Form(""),
    next: str = Form("/anasayfa"),
    dosya: UploadFile | None = File(None),
):
    target = safe_next(next)
    ajax = _is_ajax(request)

    if not CHAT_ENABLED:
        return RedirectResponse(target, status_code=303)

    active_account = resolve_active_account(request)
    if active_account is None:
        return RedirectResponse(target, status_code=303)

    account_id = active_account["id"]
    user_text = (action or metin).strip()
    # Boş bir <input type="file"> de gönderilince (kullanıcı hiç dosya
    # seçmemiş) tarayıcı yine de boş dosya adlı bir parça gönderebilir —
    # `dosya.filename` doluysa gerçekten bir dosya seçilmiş demektir.
    has_file = dosya is not None and bool(dosya.filename)

    if not user_text and not has_file:
        # Boş gönderim (metin de action da dosya da yok) — CLI'nın boş
        # Enter'ıyla aynı: sessizce yok sayılır, henüz sohbet edilmemişse
        # boş bir chat_sessions satırı bile açılmaz (bkz. get_current_chat_session_id).
        if ajax:
            return _chat_fragment_response(request, account_id)
        return RedirectResponse(target, status_code=303)

    file_bytes: bytes | None = None
    file_mime_type: str | None = None
    if has_file:
        file_bytes = dosya.file.read()
        file_mime_type = dosya.content_type or "application/octet-stream"

    lang = resolve_language(request, active_account)
    display_text = metin.strip() if metin.strip() else (
        translate("chat.file.sent_placeholder", lang) if has_file else _display_text_for_action(action, metin, lang)
    )

    cookie_carrier = Response()
    session_id = get_or_create_chat_session(request, cookie_carrier, account_id)

    in_progress = request.app.state.chat_in_progress
    if session_id not in in_progress:
        # Aynı oturum için hâlâ süren bir tur varsa (çift tıklama/iki sekme)
        # bu isteği sessizce yok say, ikinci bir calendar.create_event
        # tetiklenmesin (bkz. plan Riskler) — yine de güncel durumu göstermek
        # için aşağıda normal şekilde bir yanıt üretiliyor.
        in_progress.add(session_id)
        try:
            # Ana takvim hesabı ayarlıysa (bkz. Ayarlar), sohbetin create/
            # update/query_calendar'ı hangi hesaptan konuşuluyor olursa olsun
            # HEP o TEK hesaba gider — böylece "asistanın takvimi" tutarlı
            # tek bir yer olur, aktif hesap yalnızca mail/hesap gezinme
            # bağlamını değiştirir.
            calendar = _get_calendar(request, resolve_write_account_id(account_id))
            process_message(
                session_id,
                user_text,
                llm=request.app.state.llm,
                embedding_provider=request.app.state.embedding_provider,
                calendar=calendar,
                lang=lang,
                display_text=display_text,
                file_bytes=file_bytes,
                file_mime_type=file_mime_type,
            )
        except Exception:
            # Önceki sürüm burada YALNIZCA log yazıp geçiyordu — kullanıcının
            # mesajı zaten kaydedilmişti (process_message'ın ilk transaction'ı)
            # ama hiçbir yanıt eklenmediği için sohbette TAMAMEN SESSİZ bir
            # çökme oluyordu (canlı testte, model bir JSON listesi döndürüp
            # tek-nesne bekleyen eski koda çarptığında bulundu). State'e HİÇ
            # dokunulmuyor (advance() hiç tamamlanmadığı için zaten değişmedi) —
            # yalnızca kullanıcının bir yanıt görmesi için genel bir hata
            # balonu ekleniyor.
            logger.exception("Sohbet turu işlenemedi (session=%s)", session_id)
            try:
                with get_connection() as conn:
                    append_chat_message(conn, session_id, "assistant", translate("chat.generic_error", lang))
            except Exception:
                logger.exception("Hata balonu bile eklenemedi (session=%s)", session_id)
        finally:
            in_progress.discard(session_id)

    # session_id BURADA BİLİNİYOR (get_or_create_chat_session'dan) — fragment'ı
    # bunun üzerinden render ediyoruz, `request.cookies`'ten YENİDEN OKUMUYORUZ:
    # yeni açılan bir oturumun Set-Cookie'si henüz tarayıcıya gitmediği için
    # `request.cookies` hâlâ eski hâlini gösterir (bkz. widget_context_for_session
    # docstring'i — canlı testte bulunan bir bug).
    response = (
        _chat_fragment_response_for_session(request, session_id)
        if ajax
        else RedirectResponse(target, status_code=303)
    )
    _copy_cookies(cookie_carrier, response)
    return response


@router.post("/asistan/yeni-sohbet")
def new_chat(request: Request, next: str = Form("/anasayfa")):
    """ESKİ oturuma hiç dokunmaz (mesaj geçmişi kalıcı — bkz.
    create_new_chat_session docstring'i), yalnızca aktif cookie'yi yeni/boş
    bir oturuma çevirir. Eski sohbet `/asistan/gecmis`'te hâlâ görünür."""
    target = safe_next(next)
    ajax = _is_ajax(request)
    active_account = resolve_active_account(request)

    if active_account is None:
        if ajax:
            return _chat_fragment_response(request, None)
        return RedirectResponse(target, status_code=303)

    cookie_carrier = Response()
    session_id = create_new_chat_session(cookie_carrier, active_account["id"])

    response = (
        _chat_fragment_response_for_session(request, session_id)
        if ajax
        else RedirectResponse(target, status_code=303)
    )
    _copy_cookies(cookie_carrier, response)
    return response


@router.get("/asistan/gecmis")
def chat_history(request: Request):
    active_account = resolve_active_account(request)
    account_id = active_account["id"] if active_account else None
    active_session_id = request.cookies.get("chat_session")

    sessions = list_chat_sessions(account_id) if account_id else []
    return templates.TemplateResponse(
        request,
        "gecmis_sohbetler.html",
        {"active_page": None, "sessions": sessions, "active_session_id": active_session_id},
    )


@router.post("/asistan/sohbete-don/{session_id}")
def resume_chat_session(request: Request, session_id: str, next: str = Form("/anasayfa")):
    target = safe_next(next)
    active_account = resolve_active_account(request)
    response = RedirectResponse(target, status_code=303)
    if active_account is not None:
        switch_chat_session(response, active_account["id"], session_id)
    return response
