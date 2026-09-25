"""Giriş sayfası + çıkış (bkz. plan "Real login (Gmail/Outlook) + per-user
account ownership"). Gerçek OAuth değişimi burada YOK — o hâlâ
oauth_routes.py/outlook_oauth_routes.py'de, `niyet=giris` sorgu
parametresiyle "giriş" niyetine dallanıyor (bkz. o dosyalardaki
`oauth_intent` cookie mantığı). Bu dosya yalnızca (1) giriş EKRANINI
gösterir, (2) oturumu SONLANDIRIR."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from src.ui.auth import SESSION_COOKIE, destroy_session
from src.ui.templating import templates

router = APIRouter()

# /giris giriş yapmamış ziyaretçiye açık; sorgu parametresi kullanıcı
# kontrolünde olduğundan yalnızca bilinen hata kodları şablona geçirilir
# (catalog.py'deki `hesaplar.oauth_error.*` anahtarlarıyla aynı küme).
_KNOWN_OAUTH_ERRORS = {"reddedildi", "gecersiz", "basarisiz", "client_yok", "ms_client_yok"}


@router.get("/giris")
def login_page(request: Request, oauth_hata: str | None = None):
    # Zaten geçerli bir oturumu olan biri /giris'e gelirse (örn. geri
    # tuşu) doğrudan Ana Sayfa'ya — auth_guard_middleware bu rotayı MUAF
    # tuttuğu için (giriş yapmanın kendisi buradan geçtiği için) burada
    # kendimiz kontrol etmemiz gerekiyor.
    if request.state.user is not None:
        return RedirectResponse("/anasayfa", status_code=303)
    oauth_error = oauth_hata if oauth_hata in _KNOWN_OAUTH_ERRORS else None
    return templates.TemplateResponse(request, "giris.html", {"oauth_error": oauth_error})


@router.post("/cikis")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        destroy_session(token)
    response = RedirectResponse("/giris", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
