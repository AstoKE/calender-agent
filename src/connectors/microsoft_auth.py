"""Microsoft (Outlook mail + MS Calendar) OAuth 2.0 kimlik doğrulama
yardımcıları — src/connectors/google_auth.py'nin MSAL karşılığı, AYNI
interaktif/interaktif-olmayan ayrımı burada da geçerli (bkz. o dosyanın
notu: web sunucusu bir istek işlerken interaktif OAuth'a asla düşmemeli,
aksi halde istek sonsuza kadar bekler ve sunucu makinesinde bir tarayıcı
penceresi açmaya çalışır).

MSAL'ın (`PublicClientApplication`) Google'ın `Credentials`'ından farkı:
token'lar `SerializableTokenCache` adlı tek bir JSON blob'unda tutulur,
`Credentials.to_json()`/`from_authorized_user_file` gibi ayrı bir
nesne değil — bu yüzden Google'ınkiyle birebir aynı fonksiyon imzaları
yerine (Credentials döndürmek yerine) doğrudan access token string'i
dönülüyor, ama dosya-başına-hesap deseni (`_token_path`) AYNEN korundu.

Kurulum: Azure Portal'da kişisel bir Microsoft hesabıyla ("Yalnızca
kişisel hesaplar") bir App Registration oluşturup client ID'yi
MS_CLIENT_ID olarak .env'e ekleyin (bkz. .env.example, CLAUDE.md
Outlook bölümündeki adım adım yönerge). Client secret GEREKMİYOR
(public client — masaüstü/yerel uygulama akışı)."""

from __future__ import annotations

import os
from pathlib import Path

import msal

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Google'ın GOOGLE_ACCOUNT_SCOPES'una karşılık — mail + takvim tek seferde
# istenir (aynı gerekçe: ayrı ayrı istenirse ikinci connector "yetersiz
# izin" hatası alır, bkz. google_auth.py'deki not).
MAIL_READ_SCOPE = "https://graph.microsoft.com/Mail.Read"
CALENDARS_READWRITE_SCOPE = "https://graph.microsoft.com/Calendars.ReadWrite"
USER_READ_SCOPE = "https://graph.microsoft.com/User.Read"
MS_ACCOUNT_SCOPES = [MAIL_READ_SCOPE, CALENDARS_READWRITE_SCOPE, USER_READ_SCOPE]

# "consumers": YALNIZCA kişisel Microsoft hesapları — App Registration'ın
# "Yalnızca kişisel hesaplar" seçimiyle tutarlı olmalı (canlı testte
# doğrulandı: bu authority + bu tip App Registration ile gerçek bir Graph
# API çağrısı başarılı oldu, bkz. CLAUDE.md).
AUTHORITY = "https://login.microsoftonline.com/consumers"


def ms_client_id() -> str:
    client_id = os.environ.get("MS_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "MS_CLIENT_ID ortam değişkeni bulunamadı — .env dosyasına ekleyin "
            "(bkz. .env.example, Azure App Registration'ın istemci kimliği)."
        )
    return client_id


def _token_cache_path(account_id: str) -> Path:
    return DATA_DIR / f"ms_token_{account_id}.json"


def load_ms_token_cache(account_id: str) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    token_path = _token_cache_path(account_id)
    if token_path.exists():
        cache.deserialize(token_path.read_text(encoding="utf-8"))
    return cache


def save_ms_token_cache(account_id: str, cache: msal.SerializableTokenCache) -> None:
    """`has_state_changed` MSAL'ın kendi bayrağı — sessiz bir yenileme bile
    cache'i değiştirebileceğinden HER çağrıdan sonra kontrol edilmeli (bkz.
    MSAL dokümantasyonundaki standart desen), yalnızca ilk interaktif
    girişte değil."""
    if not cache.has_state_changed:
        return
    token_path = _token_cache_path(account_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(cache.serialize(), encoding="utf-8")
    token_path.chmod(0o600)


def _app_for(account_id: str, cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(ms_client_id(), authority=AUTHORITY, token_cache=cache)


def get_ms_token(scopes: list[str], account_id: str) -> str:
    """Verilen scope'lar için geçerli bir access token döner. Önce sessiz
    yenilemeyi (cache'te bir refresh token varsa) dener, olmazsa tarayıcı
    üzerinden kullanıcı onayı ister (`acquire_token_interactive`). YALNIZCA
    CLI'dan çağrılır — web sunucusunun interaktif OAuth'a asla düşmemesi
    gerektiği için `load_ms_token_noninteractive` (okuma) ve gelecekteki
    web-tabanlı "hesap ekle" akışı bu fonksiyonu hiç çağırmaz (bkz.
    google_auth.py'deki aynı ayrım)."""
    cache = load_ms_token_cache(account_id)
    app = _app_for(account_id, cache)

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])

    if not result:
        result = app.acquire_token_interactive(scopes)

    save_ms_token_cache(account_id, cache)

    if "access_token" not in result:
        raise RuntimeError(
            f"Microsoft OAuth başarısız: {result.get('error_description') or result.get('error')}"
        )
    return result["access_token"]


def load_ms_token_noninteractive(scopes: list[str], account_id: str) -> str | None:
    """`get_ms_token` ile aynı cache'i okur, mümkünse sessizce yeniler, ama
    `acquire_token_interactive`'e ASLA düşmez — cache boşsa/yenileme
    başarısız olursa `None` döner (bkz. google_auth.py::load_credentials_noninteractive
    ile birebir aynı güvenlik gerekçesi: Takvim gibi her sayfa yüklemesinde
    connector kuran ekranlar bunu kullanmalı, get_ms_token'ı DEĞİL)."""
    cache = load_ms_token_cache(account_id)
    app = _app_for(account_id, cache)

    accounts = app.get_accounts()
    if not accounts:
        return None

    result = app.acquire_token_silent(scopes, account=accounts[0])
    save_ms_token_cache(account_id, cache)

    if result and "access_token" in result:
        return result["access_token"]
    return None


def has_usable_ms_credentials(account_id: str, scopes: list[str] = MS_ACCOUNT_SCOPES) -> bool:
    return load_ms_token_noninteractive(scopes, account_id) is not None
