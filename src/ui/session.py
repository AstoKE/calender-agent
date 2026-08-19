"""Tarayıcı görünüm durumu: aktif dil + aktif hesap (bkz. plan "Hesap
değiştirme"). Cookie'de tutulur, DB'de DEĞİL — "aktif hesap" kalıcı bir
kullanıcı tercihi değil, tarayıcı sekmesine özgü bir görünüm durumu; DB'ye
koymak iki sekmenin aynı satır üzerinde birbirini ezmesine yol açardı.
Auth yok, dolayısıyla cookie'den başka bir oturum kavramı da yok."""

from __future__ import annotations

from fastapi import Request, Response

from src.connectors.account_registry import list_accounts
from src.localization import normalize_language
from src.localization.preferences import get_localization_preference
from src.storage.preferences import get_preference

ACCOUNT_COOKIE = "active_account"
LANGUAGE_COOKIE = "ui_lang"
COOKIE_MAX_AGE = 400 * 24 * 3600  # ~13 ay — tarayıcıların izin verdiği pratik üst sınıra yakın


def resolve_active_account(request: Request, accounts: list[dict] | None = None) -> dict | None:
    """Cookie'deki id hâlâ kayıtlıysa onu; değilse ilk hesabı (list_accounts
    zaten connected_at'e göre sıralı, deterministik); hiç hesap yoksa None
    döner. HİÇBİR durumda istisna fırlatmaz — yedi ekranın hepsi
    active_account is None ile ilk-çalıştırma durumu göstermeli, 500 değil."""
    if accounts is None:
        accounts = list_accounts()
    if not accounts:
        return None
    cookie_id = request.cookies.get(ACCOUNT_COOKIE)
    if cookie_id:
        for acc in accounts:
            if acc["id"] == cookie_id:
                return acc
    return accounts[0]


def resolve_language(request: Request, active_account: dict | None = None) -> str:
    """Sıra: ui_lang cookie -> aktif hesabın localization_preferences'ı ->
    user_preferences (hesapsız kalıcı geri düşüş) -> Accept-Language ->
    varsayılan (tr). Üçüncü katman FK'sız (user_preferences hesaba bağlı
    değil) — localization_preferences.account_id hesaba FK verdiği için
    hiç hesap yokken oraya yazılamaz, ama cookie silinse bile dil tercihi
    hayatta kalmalı (bkz. POST /dil route'unun yazma tarafı)."""
    cookie_lang = request.cookies.get(LANGUAGE_COOKIE)
    if cookie_lang:
        return normalize_language(cookie_lang)

    if active_account:
        pref = get_localization_preference(active_account["id"])
        if pref and pref.get("ui_language"):
            return normalize_language(pref["ui_language"])

    fallback_lang = get_preference("ui.language")
    if fallback_lang:
        return normalize_language(fallback_lang)

    accept_language = request.headers.get("accept-language")
    if accept_language:
        first = accept_language.split(",")[0].strip()
        return normalize_language(first)

    return normalize_language(None)


def safe_next(raw: str | None, fallback: str = "/anasayfa") -> str:
    """Açık-yönlendirme (open redirect) koruması: '/' ile başlamalı, '//' veya
    '\\' içermemeli (host-relative görünüp aslında başka bir origin'e
    yönlendiren protokolden-bağımsız URL'leri eler)."""
    if not raw or not raw.startswith("/"):
        return fallback
    if raw.startswith("//") or "\\" in raw:
        return fallback
    return raw


def set_session_cookies(response: Response, *, lang: str | None = None, account_id: str | None = None) -> None:
    if lang is not None:
        response.set_cookie(
            LANGUAGE_COOKIE, lang, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )
    if account_id is not None:
        response.set_cookie(
            ACCOUNT_COOKIE, account_id, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )
