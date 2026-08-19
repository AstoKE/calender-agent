"""Sohbet rotaları: `POST /asistan/mesaj`, `POST /asistan/sifirla` (bkz.
plan "Web Chatbox" Faz 5). Ayrı dosya — `routes.py` zaten 500+ satır.

`_get_calendar` (interaktif-OAuth-toleranslı) `routes.py`'den içe aktarılıyor,
`get_calendar_or_none` DEĞİL: bir sohbet mesajı göndermek `/oneriler/{id}/onayla`
gibi açık bir kullanıcı eylemi, Takvim'in otomatik sayfa-yükleme widget'ı gibi
değil (bkz. plan Mimari kararlar)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from src.core.logging_config import get_logger
from src.ui.chat_session import get_current_chat_session_id, get_or_create_chat_session, reset_chat_session
from src.ui.chat_state import process_message
from src.ui.routes import CHAT_ENABLED, _get_calendar
from src.ui.session import resolve_active_account, resolve_language, safe_next

router = APIRouter()
logger = get_logger("ui.chat_routes")


@router.post("/asistan/mesaj")
def send_chat_message(
    request: Request,
    metin: str = Form(""),
    action: str = Form(""),
    next: str = Form("/anasayfa"),
):
    target = safe_next(next)
    if not CHAT_ENABLED:
        return RedirectResponse(target, status_code=303)

    active_account = resolve_active_account(request)
    if active_account is None:
        return RedirectResponse(target, status_code=303)

    user_text = (action or metin).strip()
    if not user_text:
        return RedirectResponse(target, status_code=303)

    response = RedirectResponse(target, status_code=303)
    session_id = get_or_create_chat_session(request, response, active_account["id"])

    in_progress = request.app.state.chat_in_progress
    if session_id in in_progress:
        # Aynı oturum için hâlâ süren bir tur var (çift tıklama/iki sekme) —
        # bu isteği sessizce yok say, ikinci bir calendar.create_event
        # tetiklenmesin (bkz. plan Riskler).
        return response
    in_progress.add(session_id)
    try:
        lang = resolve_language(request, active_account)
        calendar = _get_calendar(request, active_account["id"])
        process_message(
            session_id,
            user_text,
            llm=request.app.state.llm,
            embedding_provider=request.app.state.embedding_provider,
            calendar=calendar,
            lang=lang,
        )
    except Exception:
        logger.exception("Sohbet turu işlenemedi (session=%s)", session_id)
    finally:
        in_progress.discard(session_id)

    return response


@router.post("/asistan/sifirla")
def reset_chat(request: Request, next: str = Form("/anasayfa")):
    target = safe_next(next)
    active_account = resolve_active_account(request)
    if active_account is not None:
        session_id = get_current_chat_session_id(request, active_account["id"])
        if session_id is not None:
            reset_chat_session(session_id)
    return RedirectResponse(target, status_code=303)
