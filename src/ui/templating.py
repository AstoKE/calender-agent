"""Jinja2Templates için TEK kurulum noktası (bkz. plan Faz 2).

context_processors sayesinde t()/lang/fmt_*/accounts/active_account her
route'un TemplateResponse çağrısına tek tek eklenmesi gerekmeden tüm
şablonlara ulaşır (Starlette 1.6.0'da doğrulandı: Jinja2Templates.__init__
context_processors=list[Callable[[Request], dict]] kabul ediyor).

routes.py'den buraya taşınmasının asıl nedeni 404 handler'ın (Faz 9'da
eklenecek) app.py'den de aynı `templates` nesnesine erişebilmesi gerekmesi
— iki ayrı Jinja2Templates örneği iki ayrı (ve tutarsız) context_processor
kümesi anlamına gelirdi."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from src.connectors.account_registry import list_accounts
from src.core.logging_config import get_logger
from src.localization import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, translator_for
from src.localization.formatting import (
    format_date,
    format_datetime,
    format_day_header,
    format_time,
    format_time_range,
)
from src.ui.nav import NAV_ITEMS
from src.ui.presenters import avatar_color, describe_structured_action, diff_snapshots, initials
from src.ui.session import resolve_active_account, resolve_language

logger = get_logger("ui.templating")


def i18n_context(request: Request) -> dict:
    try:
        active_account = getattr(request.state, "active_account", None)
        lang = resolve_language(request, active_account)
    except Exception:
        logger.exception("i18n_context başarısız oldu, varsayılan dile düşülüyor")
        lang = DEFAULT_LANGUAGE
    return {
        "lang": lang,
        "t": translator_for(lang),
        "fmt_date": lambda dt: format_date(dt, lang),
        "fmt_time": lambda dt: format_time(dt, lang),
        "fmt_datetime": lambda dt: format_datetime(dt, lang),
        "fmt_range": lambda a, b: format_time_range(a, b, lang),
        "fmt_day_header": lambda d: format_day_header(d, lang),
        "supported_languages": SUPPORTED_LANGUAGES,
    }


def shell_context(request: Request) -> dict:
    """Üst bar + rail için ortak veri. Burada ASLA Google API çağrısı
    olmamalı — her TemplateResponse'ta çalışır. try/except şart: burası
    fırlatırsa 404 sayfası dahil HER sayfa 500 verir ve özyineleme yapar."""
    try:
        accounts = getattr(request.state, "accounts", None)
        if accounts is None:
            accounts = list_accounts()
        active_account = getattr(request.state, "active_account", None)
        if active_account is None:
            active_account = resolve_active_account(request, accounts)
        return {"accounts": accounts, "active_account": active_account}
    except Exception:
        logger.exception("shell_context başarısız oldu, boş kabukla devam ediliyor")
        return {"accounts": [], "active_account": None}


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[i18n_context, shell_context],
)
templates.env.globals["NAV_ITEMS"] = NAV_ITEMS
templates.env.globals["initials"] = initials
templates.env.globals["avatar_color"] = avatar_color
templates.env.globals["describe_structured_action"] = describe_structured_action
templates.env.globals["diff_snapshots"] = diff_snapshots
