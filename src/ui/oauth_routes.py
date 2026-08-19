"""Tarayıcıda yeni Google hesabı ekleme (bkz. plan "Tarayıcıda hesap ekleme").

`google_auth.py::get_google_credentials` (CLI'nın OAuth akışı) token yoksa
`InstalledAppFlow.run_local_server`'a düşüyor — bu, BİR WEB İSTEĞİNDEN
tetiklenirse isteği sonsuza kadar bekletip SUNUCU MAKİNESİNDE bir tarayıcı
penceresi açar (bkz. CLAUDE.md'deki "Kritik düzeltme" notu — bu modülün var
olma sebebi tam olarak bu). Bunun yerine burada `google_auth_oauthlib.flow.Flow`
(InstalledAppFlow DEĞİL) ile klasik iki-adımlı web Authorization Code akışı
kullanılıyor: `/hesaplar/baglan` kullanıcının KENDİ tarayıcısını Google'ın
onay ekranına yönlendirir, Google onaydan sonra `/hesaplar/oauth/geri-don`'a
geri yönlendirir.

Google'ın "Desktop app" tipi OAuth client'ları (bu projenin
`data/google_oauth_client.json`'ı zaten bu tipte, CLI'nın InstalledAppFlow'u
için) loopback (127.0.0.1/localhost) adreslerinde rastgele PORT VE PATH'e
izin verir (RFC 8252 §7.3 loopback istisnası, Google'ın kendi belgeleriyle
doğrulandı) — yani bu akış için Google Cloud Console'da HİÇBİR ek redirect
URI kaydı gerekmiyor, mevcut client dosyası olduğu gibi kullanılabiliyor.

`state` parametresi CSRF koruması için kısa ömürlü bir cookie'de tutulur
(bkz. OAUTH_STATE_COOKIE) — geri dönüşte cookie'deki değerle eşleşmiyorsa
işlem reddedilir."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from src.connectors.account_registry import _derive_account_id, ensure_account_registered
from src.connectors.google_auth import DEFAULT_CLIENT_SECRET_PATH, GOOGLE_ACCOUNT_SCOPES, save_credentials_for_account
from src.core.logging_config import get_logger
from src.ui.session import set_session_cookies

router = APIRouter()
logger = get_logger("ui.oauth_routes")

OAUTH_STATE_COOKIE = "oauth_state"
OAUTH_STATE_MAX_AGE = 600  # 10 dk — bir onay ekranında oyalanmak için yeterli, sonsuza kadar açık kalmasın


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("oauth_callback"))


@router.get("/hesaplar/baglan")
def start_oauth(request: Request):
    if not DEFAULT_CLIENT_SECRET_PATH.exists():
        return RedirectResponse("/hesaplar?oauth_hata=client_yok", status_code=303)

    flow = Flow.from_client_secrets_file(
        str(DEFAULT_CLIENT_SECRET_PATH), scopes=GOOGLE_ACCOUNT_SCOPES, redirect_uri=_redirect_uri(request)
    )
    # prompt=consent: refresh_token'ın HER seferinde döndüğünden emin olmak
    # için (Google yalnızca ilk onayda refresh_token verir, sonrakilerde
    # sessizce atlar) — bu proje account başına kalıcı bir refresh_token'a
    # bağımlı (bkz. get_google_credentials'ın sessiz yenileme yolu).
    authorization_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    response = RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(
        OAUTH_STATE_COOKIE, state, max_age=OAUTH_STATE_MAX_AGE, path="/hesaplar/oauth",
        httponly=True, samesite="lax",
    )
    return response


def _oauth_error_redirect(reason: str) -> RedirectResponse:
    response = RedirectResponse(f"/hesaplar?oauth_hata={reason}", status_code=303)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/hesaplar/oauth")
    return response


@router.get("/hesaplar/oauth/geri-don", name="oauth_callback")
def oauth_callback(request: Request):
    if request.query_params.get("error"):
        # Kullanıcı Google'ın onay ekranında "İptal"e bastı.
        return _oauth_error_redirect("reddedildi")

    state = request.query_params.get("state")
    code = request.query_params.get("code")
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not code or not state or not cookie_state or state != cookie_state:
        logger.warning("OAuth geri dönüşü: state uyuşmadı ya da code eksik")
        return _oauth_error_redirect("gecersiz")

    flow = Flow.from_client_secrets_file(
        str(DEFAULT_CLIENT_SECRET_PATH), scopes=GOOGLE_ACCOUNT_SCOPES,
        state=state, redirect_uri=_redirect_uri(request),
    )
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        # E-posta adresini KULLANICININ TYPE ETMESİNE değil, gerçekten
        # onayladığı hesaba göre belirliyoruz (CLI'nın select_account()'ı
        # yalnızca kullanıcının yazdığı metne güveniyordu — burada Gmail
        # profilinden gerçek adres okunuyor, gmail.readonly zaten scope'ta).
        profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
        email = profile["emailAddress"]
    except Exception:
        logger.exception("OAuth token değişimi ya da profil sorgusu başarısız")
        return _oauth_error_redirect("basarisiz")

    account_id = _derive_account_id(email)
    save_credentials_for_account(account_id, creds)
    ensure_account_registered(account_id, provider="google", email=email)

    response = RedirectResponse("/hesaplar?hesap_eklendi=1", status_code=303)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/hesaplar/oauth")
    set_session_cookies(response, account_id=account_id)
    return response
