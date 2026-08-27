"""Tarayıcıda yeni Outlook/Microsoft hesabı ekleme — `src/ui/oauth_routes.py`nin
(Google) MSAL karşılığı, AYNI gerekçeyle: `microsoft_auth.py::get_ms_token`
(CLI'nın akışı) token yoksa `acquire_token_interactive`'e düşüyor, bu BİR
WEB İSTEĞİNDEN tetiklenirse isteği sonsuza kadar bekletip SUNUCU
MAKİNESİNDE bir tarayıcı penceresi açar. Bunun yerine MSAL'ın kendi web
Authorization Code akışı (`initiate_auth_code_flow`/`acquire_token_by_auth_code_flow`,
PKCE'yi kendisi hallediyor) kullanılıyor: `/hesap-ekle-outlook` kullanıcının
KENDİ tarayıcısını Microsoft'un onay ekranına yönlendirir, onaydan sonra
`/hesap-ekle-outlook/callback`'e geri döner.

`initiate_auth_code_flow`'un döndürdüğü `flow` sözlüğü (state + PKCE
code_verifier + ...) TAMAMEN JSON-serileştirilebilir — `oauth_routes.py`nin
yalnızca `state` string'ini cookie'de tutmasının aksine, burada `code_verifier`
de gerektiğinden flow'un TAMAMI kısa ömürlü bir cookie'de saklanıyor.

Redirect URI Azure App Registration'da "Mobil ve masaüstü uygulamaları"
(public client) platformu altında kayıtlı olmalı — TAM olarak
`http://localhost:8000/hesap-ekle-outlook/callback` (canlı testte 2 gerçek
bulgu: (1) "Web" platformu altına kaydedilirse Azure isteği gizli-istemci
sayıp `AADSTS70002: client_secret gerekli` hatası veriyor, "Allow public
client flows" ayarı bunu DÜZELTMİYOR — çözüm doğrudan "Mobil ve masaüstü
uygulamaları" platformuna kaydetmek; (2) Azure aynı redirect URI'nin İKİ
platformda birden kayıtlı olmasına izin vermiyor, "Web" altındaki kayıt
buraya taşınmadan önce SİLİNMELİYDİ). SADECE "localhost", `127.0.0.1`
DEĞİL — Azure HTTPS olmayan URI'lerde yalnızca tam "http://localhost" host
adını kabul ediyor, bu yüzden `_redirect_uri()` BİLEREK sabit "localhost"
döner, isteğin geldiği host'a göre dinamik ÜRETMEZ (bkz. altta)."""

from __future__ import annotations

import json
import re

import msal
import requests
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from src.connectors.account_registry import ensure_account_registered
from src.connectors.microsoft_auth import AUTHORITY, MS_ACCOUNT_SCOPES, ms_client_id, save_ms_token_cache
from src.core.logging_config import get_logger
from src.ui.session import set_session_cookies

router = APIRouter()
logger = get_logger("ui.outlook_oauth_routes")

OAUTH_FLOW_COOKIE = "outlook_oauth_flow"
OAUTH_FLOW_MAX_AGE = 600  # 10 dk — Google akışıyla aynı süre (bkz. oauth_routes.py)


def _redirect_uri() -> str:
    # BİLEREK `request.url_for(...)`  (isteğin geldiği host'a göre dinamik)
    # DEĞİL, sabit "localhost" — Azure, HTTPS olmayan redirect URI'lerde
    # YALNIZCA tam olarak "http://localhost" host adını kabul ediyor,
    # "http://127.0.0.1" gibi sayısal loopback IP'sini REDDEDİYOR (canlı
    # testte "HTTPS veya http://localhost ile başlamalıdır" hatasıyla
    # doğrulandı — Google'ın "Desktop app" tipi client'larındaki
    # herhangi-loopback-adresi istisnası burada geçerli değil, Azure
    # yalnızca "localhost" ismini istisna tutuyor, IP'yi değil). Kullanıcı
    # uygulamaya 127.0.0.1 üzerinden erişse bile bu sabit değer kullanılıyor
    # — Microsoft'un geri yönlendirmesi yine de aynı
    # sunucuya ulaşır, çünkü ikisi de aynı loopback arayüzüne çözülür.
    return "http://localhost:8000/hesap-ekle-outlook/callback"


def _derive_outlook_account_id(email: str) -> str:
    """`account_registry.py::_derive_account_id` ile AYNI fikir (e-postanın
    @ öncesi kısmı) ama BİLEREK "outlook_" önekiyle — aynı yerel parçaya
    sahip bir Google hesabıyla (örn. hem enestugac@gmail.com HEM
    enestugac@hotmail.com) `id` çakışmasını önler. `ensure_account_registered`
    id zaten varsa SESSİZCE hiçbir şey yapmıyor (idempotent kayıt) — önek
    olmadan bu, ikinci hesabın YANLIŞLIKLA birinci hesabın satırıymış gibi
    görünmesine yol açardı (canlı geliştirme sırasında fark edilip önlendi,
    bu makinede tam olarak bu çakışma ihtimali gerçekti)."""
    local_part = email.split("@")[0].lower()
    return "outlook_" + re.sub(r"[^a-z0-9_-]", "_", local_part)


@router.get("/hesap-ekle-outlook")
def start_outlook_oauth(request: Request):
    try:
        ms_client_id()
    except RuntimeError:
        return RedirectResponse("/hesaplar?oauth_hata=ms_client_yok", status_code=303)

    # token_cache AÇIKÇA SerializableTokenCache olarak veriliyor — verilmezse
    # MSAL sessizce düz (serileştirilemeyen) bir TokenCache kurar (doğrulandı:
    # `PublicClientApplication(...).token_cache`'in varsayılan tipi
    # `msal.token_cache.TokenCache`, `.serialize()` metodu YOK), callback'te
    # cache'i dosyaya yazmaya çalışınca AttributeError'a yol açardı.
    app = msal.PublicClientApplication(ms_client_id(), authority=AUTHORITY, token_cache=msal.SerializableTokenCache())
    # prompt="select_account": prompt'suz bırakılırsa Azure AD tarayıcıdaki
    # mevcut Microsoft SSO oturumunu sessizce kullanıp hesap seçtirmeden
    # doğrudan o hesabı onaylıyor (canlı testte bulundu — kullanıcı "Outlook
    # hesabı ekle"ye bastığında hiç seçim ekranı görmeden zaten o an
    # tarayıcıda oturum açık olan hesap eklenmişti). Google akışındaki
    # (oauth_routes.py) prompt="consent" ile AYNI amaç, farklı sağlayıcı
    # parametre adı.
    flow = app.initiate_auth_code_flow(MS_ACCOUNT_SCOPES, redirect_uri=_redirect_uri(), prompt="select_account")

    response = RedirectResponse(flow["auth_uri"], status_code=302)
    response.set_cookie(
        OAUTH_FLOW_COOKIE, json.dumps(flow), max_age=OAUTH_FLOW_MAX_AGE, path="/hesap-ekle-outlook",
        httponly=True, samesite="lax",
    )
    return response


def _oauth_error_redirect(reason: str) -> RedirectResponse:
    response = RedirectResponse(f"/hesaplar?oauth_hata={reason}", status_code=303)
    response.delete_cookie(OAUTH_FLOW_COOKIE, path="/hesap-ekle-outlook")
    return response


@router.get("/hesap-ekle-outlook/callback", name="outlook_oauth_callback")
def outlook_oauth_callback(request: Request):
    if request.query_params.get("error"):
        # Kullanıcı Microsoft'un onay ekranında "İptal"e bastı.
        return _oauth_error_redirect("reddedildi")

    flow_cookie = request.cookies.get(OAUTH_FLOW_COOKIE)
    if not flow_cookie:
        logger.warning("Outlook OAuth geri dönüşü: flow cookie'si eksik")
        return _oauth_error_redirect("gecersiz")

    try:
        flow = json.loads(flow_cookie)
    except json.JSONDecodeError:
        return _oauth_error_redirect("gecersiz")

    app = msal.PublicClientApplication(ms_client_id(), authority=AUTHORITY, token_cache=msal.SerializableTokenCache())
    try:
        result = app.acquire_token_by_auth_code_flow(flow, dict(request.query_params))
        if "access_token" not in result:
            logger.warning("Outlook OAuth token değişimi başarısız: %s", result.get("error_description"))
            return _oauth_error_redirect("basarisiz")

        # E-posta adresini kullanıcının yazmasına değil, gerçekten onayladığı
        # hesaba göre belirliyoruz (Google akışıyla AYNI gerekçe, bkz.
        # oauth_routes.py'deki aynı yorum).
        me = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {result['access_token']}"},
            timeout=30,
        )
        me.raise_for_status()
        profile = me.json()
        email = profile.get("mail") or profile["userPrincipalName"]
    except Exception:
        logger.exception("Outlook OAuth token değişimi ya da profil sorgusu başarısız")
        return _oauth_error_redirect("basarisiz")

    account_id = _derive_outlook_account_id(email)

    # app'in kendi (varsayılan, hiç cache verilmeden kurulan) token cache'i
    # şu ana kadarki tüm alışverişi (access+refresh token) zaten tutuyor —
    # bunu account_id'ye özel dosyaya taşıyoruz (bkz. microsoft_auth.py'nin
    # dosya-başına-hesap deseni, get_ms_token/load_ms_token_noninteractive
    # bundan sonra bu dosyayı okuyacak).
    save_ms_token_cache(account_id, app.token_cache)
    ensure_account_registered(account_id, provider="outlook", email=email)

    response = RedirectResponse("/hesaplar?hesap_eklendi=1", status_code=303)
    response.delete_cookie(OAUTH_FLOW_COOKIE, path="/hesap-ekle-outlook")
    set_session_cookies(response, account_id=account_id)
    return response
