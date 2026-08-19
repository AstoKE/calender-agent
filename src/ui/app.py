"""Web UI — FastAPI uygulaması (bkz. docs/architecture-plan.md §16/§17).

Tam sol navigasyon (§16) + yedi ekranın hepsi gerçek içerik — Ana Sayfa,
Takvim, Gelen Öneriler, E-posta Hesapları, Kurallarım, Düzeltmelerim,
Ayarlar (bkz. src/ui/routes.py). Chatbox (Ana Sayfa'daki asistan slotu)
hâlâ kapsam dışı — ayrı bir dilim.

Bu proje kişisel/yerel-öncelikli tek kullanıcı için (bkz. CLAUDE.md) — web
tarafında ayrı bir login/auth katmanı YOK, sunucu yalnızca localhost'a
bağlanır. Bağlı Google hesaplarının OAuth token'larının CLI üzerinden
(select_account()) en az bir kez oluşturulmuş olması gerekir — tarayıcıda
OAuth başlatma bu dilimde yok.

`python -m src.ui.app` ile çalıştırılır.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from src.connectors.account_registry import list_accounts
from src.core.logging_config import configure_logging
from src.providers.foundry_local import FoundryLocalEmbeddingProvider, FoundryLocalProvider
from src.storage.db import init_db
from src.ui.security import CSRFGuardMiddleware
from src.ui.session import resolve_active_account, set_session_cookies
from src.ui.templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    # LLM/embedding modelleri bir kere yükleniyor (istek başına değil) — CLI'daki
    # main()'in yaptığının aynısı. prefer_gpu=False: scan_inbox.py'deki aynı
    # gerekçe (bkz. o dosyadaki not) — web sunucusu da uzun süre ayakta kalıp
    # çok sayıda istek işleyebilir, GPU'nun birkaç çağrı sonra çökme riski
    # burada da geçerli.
    app.state.llm = FoundryLocalProvider(model_alias="qwen3-4b", prefer_gpu=False)
    app.state.embedding_provider = FoundryLocalEmbeddingProvider()
    # account_id -> GoogleCalendarConnector, ilk kullanımda oluşturulur (OAuth
    # token'ı diskte hazır olduğu sürece interaktif bir şey tetiklemez).
    app.state.calendar_connectors = {}
    # Tarama dakikalarca blokluyor (senkron istek, arka plan kuyruğu bu
    # dilimde yok) — aynı hesap için kazara ikinci bir tarama tetiklenmesini
    # (çift tıklama, iki sekme) engellemek için tek-uçuş koruması. Adversarial
    # bir senaryo değil, GIL altında set.add/discard bu amaç için yeterli.
    app.state.scan_in_progress = set()
    yield


app = FastAPI(title="Calendar Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    """Markalı, çevrilmiş 404 — 7 ekranın hepsi gerçek içerik alınca
    (bkz. plan Faz 9) routes.py'deki eski `/{page_name}` catch-all'ın
    yerini aldı. `templates` burada import edilebilir olduğu için
    (src/ui/templating.py, routes.py'den ayrı) bu handler mümkün oldu."""
    return templates.TemplateResponse(request, "404.html", {"active_page": None}, status_code=404)


def _response_already_sets_cookie(response, cookie_name: str) -> bool:
    prefix = f"{cookie_name}=".encode()
    return any(
        header.lower() == b"set-cookie" and value.startswith(prefix)
        for header, value in response.raw_headers
    )


@app.middleware("http")
async def account_session_middleware(request: Request, call_next):
    """İstek başına aktif hesabı bir kez çözer (request.state'e yazar —
    templating.py'nin context_processor'ları aynı sorguyu tekrarlamasın),
    sonra yanıt üretildikten SONRA cookie'yi gerekirse kendini onaracak
    şekilde yeniden yazar (bayat/silinmiş bir hesabı işaret eden cookie,
    her istekte sessizce ilk hesaba düşer ve cookie güncellenir).

    Route handler'ın bu TAM istekte kendi isteğiyle farklı bir hesaba
    geçmiş olabileceğini (POST /hesap-sec) hesaba katar — response zaten
    kendi Set-Cookie'sini taşıyorsa üzerine yazmaz/ikinci bir tane eklemez,
    aksi halde tarayıcı aynı isim için iki çelişen Set-Cookie alır."""
    if request.url.path.startswith("/static"):
        return await call_next(request)
    accounts = list_accounts()
    active_account = resolve_active_account(request, accounts)
    request.state.accounts = accounts
    request.state.active_account = active_account
    response = await call_next(request)
    if (
        active_account
        and request.cookies.get("active_account") != active_account["id"]
        and not _response_already_sets_cookie(response, "active_account")
    ):
        set_session_cookies(response, account_id=active_account["id"])
    return response


# CSRFGuardMiddleware account_session_middleware'den SONRA eklenmeli ki
# (Starlette'te sonra eklenen middleware en dışta olur, isteği ilk o görür)
# reddedilen bir cross-site POST için hesap sorgusu hiç çalışmasın.
app.add_middleware(CSRFGuardMiddleware)

from src.ui.routes import router  # noqa: E402 — döngüsel import'u önlemek için app tanımlandıktan sonra

app.include_router(router)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
