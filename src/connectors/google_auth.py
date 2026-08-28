"""Google OAuth 2.0 kimlik doğrulama yardımcıları (Gmail + Google Calendar ortak).

Kurulum adımları için Task 6 rehberine bakın: bir OAuth client (Desktop app)
oluşturup indirilen JSON'u ``data/google_oauth_client.json`` olarak kaydedin.
Token'lar da ``data/`` altında tutulur (gitignore'da, düz metin ama repo
dışında — tam şifreleme MVP sonrası bir iyileştirme, bkz. plan §13).
"""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_CLIENT_SECRET_PATH = DATA_DIR / "google_oauth_client.json"

# Bir Google hesabı bağlanırken (bkz. §16 "E-posta Hesapları" ekranı) tüm
# gerekli izinler TEK seferde istenir. Gmail ve Calendar connector'ları farklı
# scope alt kümeleriyle ayrı ayrı kimlik doğrulaması yapsaydı, aynı account_id
# için ikinci connector "yetersiz izin" hatası alırdı (token belirli scope'lara
# bağlıdır) — bu yüzden her iki connector da bu birleşik listeyi kullanır.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
# DOĞRULANDI (canlı test, 2026-08-04): yalnızca calendar.events ile
# freebusy.query çağrısı "403 Insufficient Permission" hatası veriyor.
# calendar.readonly eklenince çözüldü.
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_ACCOUNT_SCOPES = [GMAIL_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE, CALENDAR_READONLY_SCOPE]


def _token_path(account_id: str) -> Path:
    return DATA_DIR / f"google_token_{account_id}.json"


def save_credentials_for_account(account_id: str, creds: Credentials) -> None:
    """Token dosyasını `account_id`'ye özel yola yazar (0600 izinle — Windows'ta
    no-op ama zararsız). `get_google_credentials`'ın hem interaktif hem yenileme
    yollarıyla, hem de tarayıcıda hesap ekleme akışıyla (bkz. src/ui/oauth_routes.py
    — InstalledAppFlow'un ASLA kullanılamayacağı, kullanıcının kendi tarayıcısında
    tamamlanan ayrı bir Authorization Code akışı) PAYLAŞILAN tek yazma noktası."""
    token_path = _token_path(account_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)


def get_google_credentials(
    scopes: list[str],
    account_id: str,
    client_secret_path: Path = DEFAULT_CLIENT_SECRET_PATH,
) -> Credentials:
    """Verilen scope'lar için geçerli bir Credentials döner.

    İlk çalıştırmada tarayıcı üzerinden kullanıcı onayı ister (InstalledAppFlow);
    sonraki çalıştırmalarda ``account_id``'ye özel saklanmış token'ı kullanır ve
    gerekirse sessizce yeniler. YALNIZCA CLI'dan çağrılır — web sunucusunun
    interaktif OAuth'a asla düşmemesi gerektiği için `load_credentials_noninteractive`
    (okuma) ve `src/ui/oauth_routes.py` (tarayıcıda yeni hesap ekleme) ayrı yollar
    kullanır, bu fonksiyonu hiç çağırmaz.
    """
    token_path = _token_path(account_id)
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                # Refresh token geçersiz/iptal edilmiş — örn. Google Cloud
                # konsolunda uygulama "Testing" modundayken verilen refresh
                # token'lar 7 gün sonra otomatik geçersiz oluyor (canlı testte
                # görüldü). Çökmek yerine tarayıcı üzerinden yeniden
                # yetkilendirmeye düş.
                print("Google oturumunuzun süresi dolmuş, tarayıcıda tekrar giriş isteniyor...")

        if not refreshed:
            if not client_secret_path.exists():
                raise FileNotFoundError(
                    f"OAuth client dosyası bulunamadı: {client_secret_path}. "
                    "Task 6 rehberindeki adımları tamamlayıp credentials.json'u "
                    "bu yola kaydedin."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
            creds = flow.run_local_server(port=0)

        save_credentials_for_account(account_id, creds)

    return creds


def load_credentials_noninteractive(scopes: list[str], account_id: str) -> Credentials | None:
    """``get_google_credentials`` ile aynı token dosyasını okur ve mümkünse
    sessizce yeniler, ama token yoksa veya refresh başarısız olursa
    ``InstalledAppFlow.run_local_server``'a ASLA düşmez — ``None`` döner.

    Web sunucusu (bkz. src/ui/) bir istek işlerken interaktif OAuth
    tetiklenirse istek sonsuza kadar bekler ve sunucu makinesinde bir
    tarayıcı penceresi açar; Takvim gibi her sayfa yüklemesinde connector
    kuran ekranlar bu fonksiyonu kullanmalı, get_google_credentials'ı değil."""
    token_path = _token_path(account_id)
    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        save_credentials_for_account(account_id, creds)
        return creds

    return None
