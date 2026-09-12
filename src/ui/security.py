"""CSRF koruması (bkz. plan "Güvenlik", AUTH-03: docs/urunlesme-ve-tasarim-yol-haritasi.md).

Oturum çerezi (bkz. src/ui/auth.py) tarayıcı tarafından HER isteğe
otomatik eklendiği için (tam olarak CSRF'in istismar ettiği şey) bir POST
hâlâ kimliksiz sayılıyor — session cookie'nin varlığı tek başına "bu istek
gerçekten bu sekmeden geldi" garantisi vermiyor. Bağımsız bir CSRF token
mekanizması kurmak yerine tarayıcının kendi
gönderdiği Fetch Metadata başlığına (Sec-Fetch-Site) bakan ücretsiz bir
kontrol yeterli: başka bir origin'deki (örn. kötü niyetli bir sekmedeki)
bir sayfadan gelen istek bu başlığı 'cross-site' taşır, tarayıcı bunu
sahtelemeyi engeller. Eski tarayıcılar bu başlığı göndermeyebilir — o
durumda Origin başlığına düşülür.

AUTH-03'te bulunan gerçek boşluk: her iki başlık da yoksa istek önceden
KABUL ediliyordu ("zaten sunucu yalnızca localhost'a bağlanıyor"
savunmasıyla) — bu, korumayı fail-OPEN yapıyordu. Modern tarayıcılar
(Chrome/Firefox/Safari/Edge, ~2020+) Fetch Metadata başlıklarını HER
istekte (klasik form gönderimi dahil, yalnızca fetch/XHR değil) gönderir;
ikisi de eksikse istek ya bir tarayıcıdan gelmiyor (script/araç) ya da çok
eski bir tarayıcıdan — artık fail-CLOSED (reddediliyor). Gerçek tarayıcı
trafiğini etkilemez, yalnızca bu boşluğu kapatır."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_same_origin(request: Request) -> bool:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in ("same-origin", "none")

    origin = request.headers.get("origin")
    if origin is not None:
        return origin == f"{request.url.scheme}://{request.url.netloc}"

    return False


class CSRFGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Yalnızca POST değil, GET/HEAD/OPTIONS DIŞINDAKİ her metot kontrol
        # ediliyor (bkz. AUTH-03) — bugün uygulamada PUT/PATCH/DELETE rotası
        # yok, ama gelecekte eklenirse bu koruma otomatik kapsar.
        if request.method not in _SAFE_METHODS and not _is_same_origin(request):
            return PlainTextResponse("Cross-site istek reddedildi.", status_code=403)
        return await call_next(request)
