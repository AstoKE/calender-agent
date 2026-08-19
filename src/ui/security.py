"""CSRF koruması (bkz. plan "Güvenlik").

Bu sunucunun auth'u yok (bkz. app.py docstring) — her POST kimliksiz
localhost isteği olarak durum değiştiriyor, ve bu redesign POST yüzeyini
büyütüyor (hesap değiştirme, kural pasifleştirme, düzeltme silme...).
Bağımsız bir CSRF token mekanizması kurmak yerine tarayıcının kendi
gönderdiği Fetch Metadata başlığına (Sec-Fetch-Site) bakan ücretsiz bir
kontrol yeterli: başka bir origin'deki (örn. kötü niyetli bir sekmedeki)
bir sayfadan gelen POST bu başlığı 'cross-site' taşır, tarayıcı bunu
sahtelemeyi engeller. Eski tarayıcılar bu başlığı göndermeyebilir — o
durumda Origin başlığına düşülür; o da yoksa (araç/script) reddetmiyoruz,
zaten sunucu yalnızca localhost'a bağlanıyor."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware


def _is_same_origin(request: Request) -> bool:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in ("same-origin", "none")

    origin = request.headers.get("origin")
    if origin is not None:
        return origin == f"{request.url.scheme}://{request.url.netloc}"

    return True


class CSRFGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and not _is_same_origin(request):
            return PlainTextResponse("Cross-site POST reddedildi.", status_code=403)
        return await call_next(request)
