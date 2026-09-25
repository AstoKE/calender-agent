# Kurulum ve Çalıştırma Rehberi

Bu rehber, projeyi sıfırdan bir makinede (Windows öncelikli, macOS/Linux notlarıyla) kurup çalıştırmayı anlatır. Proje hakkında genel bilgi için [README.md](../README.md), mimari için [architecture-plan.md](architecture-plan.md).

## 1. Gereksinimler

| Gereksinim | Not |
|---|---|
| Python **3.11+** | CI 3.11 ile çalışıyor (`.github/workflows/ci.yml`). Daha yeni sürümler (örn. 3.13) geliştirme makinesinde kullanılıyor ama kilit dosyası 3.11 ile doğrulandı; sorun çıkarsa 3.11 kullan. |
| Git | Repoyu klonlamak için. |
| Gemini API anahtarı | Ücretsiz katman yeterli: https://aistudio.google.com/apikey |
| Google hesabı + Google Cloud projesi | Gmail/Takvim erişimi için OAuth istemcisi. |
| (Opsiyonel) Kişisel Microsoft hesabı | Outlook desteği için Azure App Registration. |

Python sürümünü kontrol et:

```powershell
python --version
```

## 2. Sanal ortam (venv) kurulumu

### Windows (PowerShell)

```powershell
git clone <repo-url>
cd calender-agent

python -m venv .venv
.venv\Scripts\Activate.ps1
```

`Activate.ps1` "betik çalıştırma devre dışı" hatası verirse (bir kerelik, sadece kendi kullanıcın için):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Ya da ortamı aktive etmeden doğrudan çağır: `.venv\Scripts\python.exe -m src.ui.app`

### macOS / Linux

```bash
git clone <repo-url>
cd calender-agent

python3 -m venv .venv
source .venv/bin/activate
```

> Not: `foundry-local-sdk` (yerel model) Windows'a özgü olabilir. Linux/macOS'ta önce Gemini backend'i ile çalışmayı dene ve `pip install` çıktısını kontrol et.

### Bağımlılıkları kurma

Ortam aktifken (satırın başında `(.venv)` görünür):

```powershell
python -m pip install --upgrade pip

# Önerilen: birebir test edilmiş sürümler (geçişli bağımlılıklar dahil)
pip install -r requirements-lock.txt

# Alternatif: gevşek üst-düzey liste (yeni sürümleri çekebilir)
# pip install -r requirements.txt
```

`requirements-lock.txt` CI'ın da kullandığı dosyadır. Bir bağımlılığı güncellersen kilidi yeniden üret:

```powershell
pip freeze > requirements-lock.txt
```

Ortamdan çıkmak için `deactivate`. Ortamı sıfırlamak için `.venv` klasörünü silip yukarıdaki adımları tekrarla.

## 3. Yapılandırma (`.env`)

`.env.example` dosyasını `.env` olarak kopyala (`.env` git'e girmez):

```powershell
Copy-Item .env.example .env
```

Asgari ayar (Gemini ile çalışmak için):

```
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<aistudio.google.com'dan aldığın anahtar>
```

Diğer değişkenler (hepsi opsiyonel):

| Değişken | Ne işe yarar |
|---|---|
| `LLM_PROVIDER` | `gemini` ya da boş/`foundry_local`. Boşsa yerel Foundry Local modeli kullanılır (ilk çalıştırmada model indirir). Foto/PDF/ses özellikleri yalnızca `gemini` ile açılır. |
| `MS_CLIENT_ID` | Outlook desteği için Azure uygulama (client) ID'si. |
| `TOKEN_ENCRYPTION_KEY` | OAuth token dosyalarını diskte şifreler. Üretmek için: `python -m src.core.token_crypto`. Bir kez ayarla, sonra değiştirme/kaybetme (şifreli token'lar çözülemez, hesapları yeniden bağlaman gerekir). |
| `COOKIE_SECURE` | Yalnızca gerçek HTTPS arkasında `true` yap. Yerelde (`http://localhost`) **ayarlama**, yoksa giriş yapamazsın. |
| `LOG_CONTENT` | `full` → `data/debug.log` içinde ham içerik görünür (yalnızca yerel hata ayıklama için). |

## 4. Google OAuth istemcisi (Gmail + Takvim)

1. https://console.cloud.google.com → yeni proje → **Gmail API** ve **Google Calendar API**'yi etkinleştir.
2. OAuth consent screen → External → kendi Gmail adresini **Test user** olarak ekle.
3. Scope'lar: `gmail.readonly`, `calendar.events`, `calendar.readonly`.
4. OAuth client oluştur, tip **Desktop app**. JSON'u indirip şuraya kaydet:

   ```
   data/google_oauth_client.json
   ```

   (`data/` klasörü yoksa oluştur. Git'e girmez.)

"Testing" modunda refresh token 7 gün sonra geçersiz olur; uygulama bunu algılayıp yeniden yetkilendirme ister.

### (Opsiyonel) Outlook

Kişisel bir Microsoft hesabıyla Azure Portal → Uygulama kayıtları → "Yalnızca kişisel Microsoft hesapları" → platform: **Mobil ve masaüstü uygulamaları**, redirect URI `http://localhost` → API izinleri: `Mail.Read`, `Calendars.ReadWrite`, `offline_access`, `User.Read`. Client secret gerekmez. Application (client) ID'yi `.env` içindeki `MS_CLIENT_ID`'ye yaz.

Uygulama Outlook redirect'ini sabit `http://localhost:8000/hesap-ekle-outlook/callback` olarak kullanır; tarayıcıdan `localhost:8000` ile eriş.

## 5. Çalıştırma

Ortam aktifken, repo kök dizininden:

```powershell
# Web arayüzü (önerilen)
python -m src.ui.app
```

Tarayıcıda **http://localhost:8000/** aç (Outlook kullanacaksan `127.0.0.1` yerine `localhost`). İlk açılışta `/giris` sayfasında Gmail veya Outlook ile giriş yaparsın; ilk giriş aynı zamanda kayıt yerine geçer.

Sunucu **otomatik yeniden yükleme yapmaz**. Kodu değiştirince `Ctrl+C` ile durdurup yeniden başlat.

CLI alternatifleri:

```powershell
python -m src.services.vertical_prototype   # konuşarak etkinlik oluşturma / sorgulama
python -m src.services.scan_inbox           # gelen kutusunu tara (onay web'den yapılır)
```

Bu iki CLI ilk çalışmada kendi tarayıcı OAuth penceresini açabilir; web arayüzü bunu yapmaz.

## 6. Testler

```powershell
python -m pytest tests/ -q
python -m pyflakes src/ tests/
```

Testler gerçek `.env`, ağ, OAuth token'ları ve `data/` klasörüne dokunmaz (kalıcı izolasyon, bkz. TEST-01); gerçek anahtarların olduğu makinede de güvenle çalıştırılabilir.

## 7. Verilerin yeri

Hepsi `data/` altında ve git'e dahil değildir:

| Dosya | İçerik |
|---|---|
| `calendar_agent.db` | SQLite veritabanı (kullanıcılar, hesaplar, öneriler, kurallar, sohbetler). İlk çalıştırmada otomatik oluşur/migrate edilir. |
| `google_oauth_client.json` | Uygulamanın Google OAuth istemci kaydı (senin koyduğun). |
| `google_token_*.json`, `ms_token_*.json` | Hesap başına OAuth token'ları (`TOKEN_ENCRYPTION_KEY` varsa şifreli). |
| `debug.log` | LLM çağrıları ve karar noktaları (varsayılan olarak içerik maskeli, ~10 MB'ta döner). |

Yedek almak için uygulamayı durdurup `data/` klasörünü kopyalaman yeterli.

## 8. Sık karşılaşılan sorunlar

| Belirti | Çözüm |
|---|---|
| Uygulama açılıyor ama LLM çağrıları hata veriyor | `.env` içinde `LLM_PROVIDER=gemini` ve geçerli `GOOGLE_API_KEY` var mı? Uygulamayı yeniden başlattın mı? |
| "OAuth client dosyası bulunamadı" / `client_yok` | `data/google_oauth_client.json` yok ya da yanlış yerde. |
| Giriş yapıyorum ama hemen `/giris`'e dönüyorum | `.env`'de `COOKIE_SECURE=true` kalmış olabilir; yerelde kaldır. |
| Google'da `Missing code verifier` | Güncel kodu çektiğinden emin ol (düzeltme `oauth_routes.py`'de). |
| Outlook: `ModuleNotFoundError: msal` | Ortam aktif değil ya da `pip install -r requirements-lock.txt` çalışmadı. |
| Şifreli token'lar okunamıyor | `TOKEN_ENCRYPTION_KEY` değişmiş/kaybolmuş; anahtarı geri koy ya da hesabı yeniden bağla. |
| `database is locked` | Aynı `data/calendar_agent.db`'yi birden çok sunucu süreciyle açma; tek süreç çalıştır. |
| Tarama Gmail kotası hatası veriyor | Birkaç dakika bekleyip tekrar tara; ilerleme korunur. |
| Beklenmedik davranış | Önce `data/debug.log`'un son satırlarına bak: `Get-Content data\debug.log -Tail 50` |

## 9. Docker (VPS)

Konteyner yalnızca **Gemini** backend'i ile çalışır (`requirements-docker.txt` yerel model paketlerini içermez). Servisler: `app` (uvicorn, tek worker) ve `caddy` (HTTPS, sertifikayı otomatik alır). Uygulama portu dışarı açılmaz, yalnızca Caddy 80/443'ü yayınlar.

### Ön koşullar (kod dışı, sende)

1. Bir alan adı VPS'in IP'sine yönlenmiş olmalı; sunucuda 80 ve 443 açık olmalı.
2. Google Cloud'da **Web application** tipinde yeni bir OAuth client aç. Desktop client'ın loopback istisnası gerçek bir domain'de çalışmaz. Redirect URI olarak `https://<alan-adin>/hesaplar/oauth/geri-don` ekle.
3. (Outlook kullanacaksan) Azure'da `https://<alan-adin>/hesap-ekle-outlook/callback` adresini de kaydet.

### Kurulum

```bash
git clone <repo-url> && cd calender-agent
cp .env.example .env      # düzenle: aşağıdaki değişkenler
docker compose up -d --build
```

`.env` içinde en az:

```
LLM_PROVIDER=gemini
GOOGLE_API_KEY=...
DOMAIN=asistan.example.com
TOKEN_ENCRYPTION_KEY=...          # python -m src.core.token_crypto ile üret
MS_CLIENT_ID=...                  # Outlook kullanacaksan
MS_REDIRECT_URI=https://asistan.example.com/hesap-ekle-outlook/callback
```

Web OAuth client JSON'unu volume'a koy (uygulama `/app/data/google_oauth_client.json` arar):

```bash
docker compose cp google_oauth_client.json app:/app/data/google_oauth_client.json
```

`docker compose cp` dosyayı root sahipliğiyle bırakabilir; okunamazsa `docker compose exec -u root app chown app:app /app/data/google_oauth_client.json` çalıştır.

### COOKIE_SECURE sırası (önemli)

Önce `https://<alan-adin>/giris` adresinin gerçekten HTTPS ile açıldığını doğrula, **sonra** `.env`'e `COOKIE_SECURE=true` ekleyip `docker compose up -d` çalıştır. HTTPS çalışmadan set edersen tarayıcı çerezi kabul etmez ve kimse giriş yapamaz.

### İşletim

```bash
docker compose logs -f app          # uygulama logları
curl -s https://<alan-adin>/saglik  # {"status":"ok"}
docker compose down                 # durdur (veri volume'da kalır)
```

Veri `app_data` volume'undadır (SQLite, token'lar, `debug.log`). Yedek için uygulama çalışırken tutarlı bir SQLite kopyası:

```bash
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/app/data/calendar_agent.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d)"
docker compose cp app:/app/data/backup.db ./backup-$(date +%F).db
```

`TOKEN_ENCRYPTION_KEY`'i yedeği aldığın yerden **ayrı** sakla. Anahtarsız yedekteki token'lar çözülemez.
