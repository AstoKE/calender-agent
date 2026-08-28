# Calendar Agent

RAG destekli, çok dilli (TR/EN), kişiselleştirilebilir e-posta ve takvim asistanı. Microsoft Foundry Local üzerinde tamamen yerel/offline bir LLM ile çalışır — mail/takvim içeriği varsayılan olarak hiçbir bulut LLM API'sine gönderilmez (isteğe bağlı bir Gemini API entegrasyonu var, bkz. aşağıda). Kullanıcı doğal dilde konuşarak takvim etkinliği oluşturabilir, kendi kurallarını tanımlayabilir, Gmail/Outlook gelen kutusunu taratıp takvimlik içerikleri öneri olarak görebilir — **hiçbir takvim yazma işlemi açık kullanıcı onayı olmadan gerçekleşmez.**

Tam mimari, veri modeli ve tasarım kararları için **[docs/architecture-plan.md](docs/architecture-plan.md)**.
Proje geçmişi, güncel implementasyon durumu ve canlı testte öğrenilenler için **[CLAUDE.md](CLAUDE.md)**.

## Ekran görüntüleri

<table>
<tr>
<td width="50%"><img src="docs/screenshots/anasayfa.png" alt="Ana Sayfa" /><br/><sub>Ana Sayfa — bugünkü etkinlikler, bekleyen öneriler ve sohbet asistanı</sub></td>
<td width="50%"><img src="docs/screenshots/takvim.png" alt="Takvim" /><br/><sub>Takvim — haftalık saat ızgarası</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/oneriler.png" alt="Gelen Öneriler" /><br/><sub>Gelen Öneriler — mailden çıkarılan takvimlik içerikler</sub></td>
<td width="50%"><img src="docs/screenshots/kurallarim.png" alt="Kurallarım" /><br/><sub>Kurallarım — doğal dilde tanımlanan kişisel kurallar</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/giris.png" alt="Giriş ekranı" /><br/><sub>Giriş — Gmail/Outlook ile uygulama girişi</sub></td>
<td width="50%"></td>
</tr>
</table>

## Ne yapıyor (şu an)

- **Web arayüzü** (`python -m src.ui.app`) — Ana Sayfa, Takvim (haftalık saat ızgarası), Gelen Öneriler, Kurallarım, Düzeltmelerim, E-posta Hesapları, Ayarlar; TR/EN çok dilli, açık/koyu tema.
- **Gmail ile giriş yapıp Gmail/Outlook OAuth ile** e-posta+takvim hesapları bağlama; birden fazla hesabı tek bir hesapta toplayıp bunlardan birini "ana takvim hesabı" olarak seçme.
- **Web Chatbox** (Ana Sayfa'daki "Asistana yaz") ve CLI'nin ikisinden de: doğal dilde ("Türkçe/İngilizce karışık") konuşarak takvim etkinliği oluşturma, "yarın takvimimde neler var" gibi sorguları yanıtlama, var olan bir etkinliği güncelleme/iptal etme.
- Doğal dilde kişisel kural tanımlama ("toplantılar için varsayılan süre 60 dakikadır") ve bunların RAG retrieval ile otomatik uygulanması.
- **Adaptive Correction Memory** — bir öneriyi reddedip nedenini belirtmek ya da bir alanı düzenleyip onaylamak, "gelecekte de uygulayayım mı?" sorusuyla otomatik bir kurala dönüşebilir.
- Çakışma kontrolü + alternatif saat önerisi.
- Gmail **ve Outlook** gelen kutusunu tarayıp takvimlik içerikleri (toplantı daveti, randevu, son tarih vb.) öneri olarak çıkarma; reklam/bülten içeriklerini otomatik eleme.
- Fotoğraf/PDF'den (davetiye, bilet, program) çoklu etkinlik çıkarma ve sesli mesajdan yazıya çevirme (yalnızca isteğe bağlı Gemini backend'i aktifken).
- Her takvim yazma işlemi öncesi zorunlu kullanıcı onayı — hiçbir öneri sessizce takvime yazılmaz.

## Gereksinimler

- **Python 3.11+**
- Git
- Bir Google hesabı (Gmail + Google Calendar okuma/yazma izni) — Outlook/Microsoft hesap desteği isteğe bağlı, ayrıca bir Azure App Registration gerektirir (bkz. aşağıda)
- ~5 GB boş disk (yerel LLM modelleri bir kere indirilir, `~/.calendar-agent/cache/models` altına)
- (Opsiyonel) NVIDIA GPU — yoksa CPU'da da çalışır, sadece daha yavaş; GPU varsa otomatik kullanılır (bkz. [CLAUDE.md](CLAUDE.md) "GPU/CUDA")

## Kurulum

### 1. Repoyu klonla

```bash
git clone <repo-url>
cd calender-agent
```

### 2. Sanal ortam oluştur

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```
> PowerShell script çalıştırmayı engelliyorsa (`execution of scripts is disabled`), yönetici PowerShell'de bir kere: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

**Windows (cmd.exe):**
```cmd
py -3 -m venv .venv
.venv\Scripts\activate.bat
```

### 3. Bağımlılıkları kur

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Bu adım `foundry-local-sdk`'yı da kurar. Foundry Local'ın native çekirdeği (`foundry-local-core`) platforma özgü bir wheel olarak gelir (Linux'ta `.so`, Windows'ta `.dll` tabanlı) — pip bunu otomatik doğru platform için seçer, ekstra bir şey yapmanıza gerek yok.

### 4. Google OAuth kurulumu (Gmail + Calendar erişimi için)

**Zaten bir Google Cloud OAuth client'ınız varsa** (bu projeyi başka bir makinede daha önce kurduysanız): o makinedeki `data/google_oauth_client.json` dosyasını (güvenli bir yolla — USB, şifreli aktarım vb., **git ile değil**, bu dosya kasıtlı olarak repoya dahil değil) bu makinedeki aynı yola kopyalayın ve adım 5'e geçin.

**İlk kurulumsa:**

1. [console.cloud.google.com](https://console.cloud.google.com) → yeni proje oluştur
2. **APIs & Services → Library** → `Gmail API` ve `Google Calendar API`'yi ayrı ayrı etkinleştir
3. **APIs & Services → OAuth consent screen** → User Type: External → uygulama bilgilerini gir
4. **Audience** sekmesinde kendi Gmail adresinizi **Test users** olarak ekleyin (uygulama doğrulanmamış/"Testing" modunda kalacak, bu MVP için yeterli)
5. **Data access** sekmesinde şu üç scope'u ekleyin:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/calendar.readonly` (freebusy sorguları için gerekli — yalnızca `calendar.events` yetersiz kalıyor)
6. **Clients → Create client** → Application type: **Desktop app**
7. Oluşan client için JSON'u indirip şu tam yola kaydedin: `data/google_oauth_client.json`

> **Not:** Uygulama Google'ın doğrulama sürecinden geçmediği için (Testing modu), refresh token'lar **7 gün sonra otomatik olarak geçersiz olur**. Program bunu algılayıp yeniden giriş ister — bu normaldir, tekrar OAuth kurulumu yapmanıza gerek yoktur.

### 5. (Opsiyonel) Outlook/Microsoft hesap desteği

Yalnızca Outlook mail/takvim bağlamak isterseniz gerekli — Gmail'siz kurulumda bu adımı atlayabilirsiniz.

1. [portal.azure.com](https://portal.azure.com) → **Uygulama kayıtları** → **Yeni kayıt** → **kişisel bir Microsoft hesabıyla** (outlook.com/hotmail.com/live.com — kurumsal/okul hesabı değil)
2. Desteklenen hesap türleri: **"Yalnızca kişisel Microsoft hesapları"**
3. Kimlik doğrulama → **"Mobil ve masaüstü uygulamaları"** platformu → redirect URI: `http://localhost`
4. API izinleri (Microsoft Graph, delegated): `Mail.Read`, `Calendars.ReadWrite`, `offline_access`, `User.Read`
5. Client secret **gerekmiyor** (public client)
6. `.env` dosyasında `MS_CLIENT_ID=<Application (client) ID>` (bkz. [.env.example](.env.example))

### 6. (Opsiyonel) Bulut LLM backend'i (Gemini)

Varsayılan tamamen yerel/offline'dır — bu adım isteğe bağlı bir sapmadır (foto/PDF'den etkinlik çıkarma ve sesli mesaj gibi özellikler yalnızca bu backend aktifken çalışır). `.env` dosyasında:

```
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<aistudio.google.com/apikey adresinden ücretsiz anahtar>
```

### 7. Çalıştır

```bash
# Web arayüzü (önerilen — Ana Sayfa/Takvim/Öneriler/Kurallarım/... hepsi burada)
python -m src.ui.app   # http://127.0.0.1:8000/

# CLI alternatifleri
python -m src.services.vertical_prototype   # konuşarak etkinlik ekleme / takvim sorgulama
python -m src.services.scan_inbox           # gelen kutusunu tarama (yalnızca kuyruğa yazar, onay web'den)
```

İlk çalıştırmada:
- Web arayüzünde `/giris`'te Gmail/Outlook ile giriş yapmanız istenir; ilk hesap ekleme bir tarayıcı sekmesi açıp izin ister ("Google didn't verify this app" uyarısı normaldir — Advanced → devam et)
- Yerel LLM modelleri otomatik indirilir (qwen3-4b ~2.7 GB, qwen3-embedding-0.6b ~495 MB) — bu adım internet gerektirir ve birkaç dakika sürebilir, sonraki çalıştırmalarda tekrar inmez

## Sorun giderme

- **Bir şey beklenmedik davranıyor / neden bilinmiyor:** `data/debug.log` dosyasına bakın — her LLM çağrısının ham girdi/çıktısı ve karar noktaları orada loglanır.
- **`FOREIGN KEY constraint failed`:** `src/storage/db.py`'deki `init_db()` şemayı ve gerekli ad-hoc migration'ları otomatik uygular; her giriş noktası bunu zaten çağırıyor.
- **OAuth `invalid_grant` hatası:** Yukarıdaki 7 günlük token notuna bakın — program otomatik olarak yeniden yetkilendirme ister.
- **Yavaş çalışıyor:** GPU yoksa/kullanılamıyorsa yerel model CPU'da çalışır (~5-20 sn/sorgu) — bu beklenen bir düşüş, hata değildir.

## Proje yapısı

```
src/
  core/          # Pydantic domain modelleri + logging_config.py
  providers/     # LLM/Embedding provider soyutlaması, Foundry Local + opsiyonel Gemini backend'i
  connectors/    # Gmail, Outlook, Google Calendar, MS Calendar, OAuth, Account Registry
  services/      # intent/extraction/timeutil, availability (conflict engine), mail_sync/
                 # mail_analysis, calendar_view, vertical_prototype (CLI akışı),
                 # chat_flow (Web Chatbox durum makinesi), scan_inbox
  candidates/    # Candidate Event Queue (Gelen Öneriler) persistence
  policies/      # Policy Store (kişisel kurallar) — CRUD + doğal dilden türetme
  memory/        # Adaptive Correction Memory
  rag/           # Embedding tabanlı politika/düzeltme retrieval'ı
  storage/       # SQLite şema + migration + generic preferences
  localization/  # TR/EN string tablosu + tarih/saat biçimlendirme
  ui/            # FastAPI + Jinja2 Web UI, giriş/oturum, Web Chatbox HTTP katmanı
tests/           # pytest — deterministik servis/store testleri + TestClient tabanlı Web UI testleri
data/            # SQLite DB, OAuth token/client dosyaları, debug.log — git'e dahil DEĞİL
docs/            # Mimari plan, program dokümanı, ekran görüntüleri
```
