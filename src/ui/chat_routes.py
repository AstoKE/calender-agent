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

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse

from src.core.logging_config import get_logger
from src.localization import translate
from src.ui.chat_session import (
    create_new_chat_session,
    get_or_create_chat_session,
    list_chat_sessions,
    switch_chat_session,
)
from src.ui.chat_state import build_chat_widget_context, process_message, widget_context_for_session
from src.ui.routes import CHAT_ENABLED, _get_calendar
from src.ui.session import resolve_active_account, resolve_language, safe_next
from src.ui.templating import templates

router = APIRouter()
logger = get_logger("ui.chat_routes")


def _is_ajax(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def _chat_fragment_response(request: Request, account_id: str | None):
    return templates.TemplateResponse(
        request, "partials/_asistan_chat.html", build_chat_widget_context(request, account_id)
    )


def _chat_fragment_response_for_session(request: Request, session_id: str | None):
    return templates.TemplateResponse(
        request, "partials/_asistan_chat.html", widget_context_for_session(session_id)
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

    if not user_text:
        # Boş gönderim (metin de action da yok) — CLI'nın boş Enter'ıyla
        # aynı: sessizce yok sayılır, henüz sohbet edilmemişse boş boş bir
        # chat_sessions satırı bile açılmaz (bkz. get_current_chat_session_id).
        if ajax:
            return _chat_fragment_response(request, account_id)
        return RedirectResponse(target, status_code=303)

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
            lang = resolve_language(request, active_account)
            calendar = _get_calendar(request, account_id)
            process_message(
                session_id,
                user_text,
                llm=request.app.state.llm,
                embedding_provider=request.app.state.embedding_provider,
                calendar=calendar,
                lang=lang,
                display_text=_display_text_for_action(action, metin, lang),
            )
        except Exception:
            logger.exception("Sohbet turu işlenemedi (session=%s)", session_id)
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
