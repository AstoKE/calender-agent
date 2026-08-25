"""Takvim ekranı için OAuth-güvenli connector erişimi (bkz. plan Faz 4/5).

`routes.py::_get_calendar`'dan (onayla akışı) FARKLI: o `get_google_credentials`
kullanıyor ve token yoksa/geçersizse interaktif OAuth'a (tarayıcı açan
`InstalledAppFlow.run_local_server`) düşebilir — kullanıcı zaten "onayla"
tıkladığı için bu kabul edilebilir bir bekleyiş. Takvim ekranı ise HER
sayfa yüklemesinde tetiklendiği için asla interaktif OAuth'a düşmemeli;
token yoksa/geçersizse sessizce None döner, ekranın kendi "bağlantı
yenilenmeli" durumunu (bkz. takvim.html) göstermesine izin verir."""

from __future__ import annotations

from fastapi import Request

from src.connectors.account_registry import get_account
from src.connectors.google_auth import GOOGLE_ACCOUNT_SCOPES, load_credentials_noninteractive
from src.connectors.google_calendar import GoogleCalendarConnector
from src.connectors.microsoft_auth import MS_ACCOUNT_SCOPES, load_ms_token_noninteractive
from src.connectors.ms_calendar import MSCalendarConnector


def get_calendar_or_none(request: Request, account_id: str) -> GoogleCalendarConnector | MSCalendarConnector | None:
    cache = request.app.state.calendar_connectors
    cached = cache.get(account_id)
    if cached is not None:
        return cached

    account = get_account(account_id)
    provider = account["provider"] if account else "google"  # geriye dönük varsayılan (bkz. routes.py::_get_calendar)

    if provider == "outlook":
        token = load_ms_token_noninteractive(MS_ACCOUNT_SCOPES, account_id)
        if token is None:
            return None
        connector = MSCalendarConnector(account_id=account_id, access_token=token)
    else:
        creds = load_credentials_noninteractive(GOOGLE_ACCOUNT_SCOPES, account_id)
        if creds is None:
            return None
        connector = GoogleCalendarConnector(account_id=account_id, credentials=creds)

    cache[account_id] = connector
    return connector
