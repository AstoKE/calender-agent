# Calendar Agent

RAG destekli, çok dilli, kişiselleştirilebilir e-posta ve takvim asistanı. Microsoft Foundry Local üzerinde tamamen yerel (offline) bir LLM ile çalışır — mail/takvim içeriği hiçbir zaman bir bulut LLM API'sine gönderilmez.

Tam mimari, veri modeli ve tasarım kararları için **[docs/architecture-plan.md](docs/architecture-plan.md)**.
Proje geçmişi, güncel durum ve Claude Code için proje bağlamı için **[CLAUDE.md](CLAUDE.md)**.

## Ne yapıyor (şu an)

- Doğal dilde ("Türkçe/İngilizce karışık) konuşarak takvim etkinliği oluşturma
- "Yarın takvimimde neler var" gibi sorguları yanıtlama
- Doğal dilde kişisel kural tanımlama ("toplantılar için varsayılan süre 60 dakikadır") ve bunların otomatik uygulanması (RAG retrieval)
- Çakışma kontrolü + alternatif saat önerisi
- Gmail gelen kutusunu tarayıp takvimlik içerikleri (toplantı daveti, randevu, son tarih vb.) öneri olarak çıkarma, reklam/bülten içeriklerini otomatik eleme
- Her takvim yazma işlemi öncesi zorunlu kullanıcı onayı

## Gereksinimler

- **Python 3.11+** (geliştirme 3.13 ile yapıldı)
- Git
- Bir Google hesabı (Gmail + Google Calendar okuma/yazma izni)
- ~5 GB boş disk (yerel LLM modelleri bir kere indirilir, `~/.calendar-agent/cache/models` altına)
- (Opsiyonel) NVIDIA GPU — yoksa CPU'da da çalışır, sadece daha yavaş (bkz. [CLAUDE.md](CLAUDE.md) "Bilinen sorunlar")

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

**Zaten bir Google Cloud OAuth client'ınız varsa** (bu projeyi başka bir makinede daha önce kurduysanız): o makinedeki `data/google_oauth_client.json` dosyasını (güvenli bir yolla — USB, şifreli aktarım vb., **git ile değil**, bu dosya kasıtlı olarak repoya dahil değil) bu makinedeki aynı yola kopyalayın ve adım 6'ya geçin.

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

> **Not:** Uygulama Google'ın doğrulama sürecinden geçmediği için (Testing modu), refresh token'lar **7 gün sonra otomatik olarak geçersiz olur**. Program bunu algılayıp tarayıcıda otomatik yeniden giriş ister — bu normaldir, tekrar OAuth kurulumu yapmanıza gerek yoktur.

### 5. Çalıştır

```bash
# Linux/macOS
.venv/bin/python -m src.services.vertical_prototype   # konuşarak etkinlik ekleme / takvim sorgulama
.venv/bin/python -m src.services.scan_inbox            # gelen kutusunu tarama

# Windows
.venv\Scripts\python.exe -m src.services.vertical_prototype
.venv\Scripts\python.exe -m src.services.scan_inbox
```

İlk çalıştırmada:
- Bir tarayıcı sekmesi açılıp Google hesabınıza giriş/izin ister ("Google didn't verify this app" uyarısı normaldir — Advanced → devam et)
- Yerel LLM modelleri otomatik indirilir (qwen3-4b ~2.7 GB, qwen3-embedding-0.6b ~495 MB) — bu adım internet gerektirir ve birkaç dakika sürebilir, sonraki çalıştırmalarda tekrar inmez

## Sorun giderme

- **Bir şey beklenmedik davranıyor / neden bilinmiyor:** `data/debug.log` dosyasına bakın — her LLM çağrısının ham girdi/çıktısı ve karar noktaları orada loglanır.
- **`FOREIGN KEY constraint failed`:** `src/storage/db.py`'deki `init_db()` şemayı ve gerekli ad-hoc migration'ları otomatik uygular; her giriş noktası (`main()`) bunu zaten çağırıyor.
- **OAuth `invalid_grant` hatası:** Yukarıdaki 7 günlük token notuna bakın — program otomatik olarak yeniden yetkilendirme ister.
- **Yavaş çalışıyor:** Yerel model CPU'da çalışıyorsa normal (~5-20 sn/sorgu). GPU hızlandırma henüz çözülmedi, bkz. [CLAUDE.md](CLAUDE.md) "Bilinen sorunlar".

## Proje yapısı

```
src/
  core/          # Pydantic domain modelleri (CandidateEvent, UnifiedEmail, ...)
  providers/     # LLM/Embedding provider soyutlaması + Foundry Local implementasyonu
  connectors/    # Gmail, Google Calendar, OAuth, Account Registry
  services/      # Conversation/orkestrasyon: intent, extraction, mail_sync, mail_analysis,
                 # availability (conflict engine), vertical_prototype (ana akış), scan_inbox
  policies/      # Policy Store (kişisel kurallar)
  rag/           # RAG retrieval (embedding tabanlı politika arama)
  storage/       # SQLite şema + migration
tests/
data/            # SQLite DB, OAuth token/client dosyaları, debug.log — git'e dahil DEĞİL
docs/            # Mimari plan ve program dokümanı
```
