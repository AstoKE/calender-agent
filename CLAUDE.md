# Calendar Agent — Proje Bağlamı

Bu dosya, bu repoda çalışan herhangi bir Claude Code oturumunun projeyi hızlıca anlaması için yazıldı (özellikle başka bir makinede ilk kez açıldığında). Kullanıcı ile önceki oturumlarda edinilen bağlamın (hafıza/memory sistemi bu makineye özgüdür, git ile taşınmaz) yerini kısmen tutar.

## Proje nedir

RAG destekli, çok dilli (TR/EN), kişiselleştirilebilir e-posta ve takvim asistanı. Microsoft Foundry Local üzerinde tamamen yerel/offline LLM ile çalışır. Kullanıcı doğal dilde konuşarak takvim etkinliği oluşturabilir, kendi kurallarını tanımlayabilir, Gmail'ini taratıp takvimlik içerikleri öneri olarak görebilir — **hiçbir takvim yazma işlemi açık kullanıcı onayı olmadan gerçekleşmez.**

Tam mimari, veri modeli, 26 başlıklı tasarım kararları için **[docs/architecture-plan.md](docs/architecture-plan.md)** — bu doküman program dokümanı (Microsoft Foundry Local summer school planı) temel alınarak yazıldı ve projenin "anayasası" niteliğinde. Bu CLAUDE.md o planın yerini tutmaz, güncel implementasyon durumunu ve canlı testte öğrenilenleri özetler.

## Güncel durum (implementasyon)

Çalışan ve gerçek Gmail/Google Calendar hesabıyla test edilmiş:
- Core Pydantic modelleri + 17 tablolu SQLite şeması (`src/core/models.py`, `src/storage/schema.sql`)
- LLM/Embedding provider soyutlaması + Foundry Local implementasyonu (`src/providers/`)
- Gmail + Google Calendar connector'ları, OAuth (`src/connectors/`)
- Niyet tespiti: create_event / query_calendar / update_event (stub) / define_policy / other (`src/services/intent.py`)
- Konuşarak etkinlik oluşturma, eksik/belirsiz alan netleştirme, çakışma kontrolü + alternatif öneri (`src/services/vertical_prototype.py`, `src/services/availability.py`)
- Policy Store + RAG retrieval: doğal dilde kural tanımlama, embedding tabanlı retrieval, Rule Engine'in bunu deterministik uygulaması (`src/policies/`, `src/rag/`)
- Mail analizi + Candidate Queue: Gmail tarama, takvimlik/değil sınıflandırma, candidate çıkarımı, mevcut onay akışının yeniden kullanılması (`src/services/mail_sync.py`, `src/services/mail_analysis.py`, `src/services/scan_inbox.py`)
- Loglama sistemi: her LLM çağrısı + karar noktası `data/debug.log`'a yazılıyor (`src/core/logging_config.py`) — **bir şey beklenmedik davranırsa önce buraya bak, tahmin etmeye çalışma.**

Henüz yok (plan §19/§25'e göre sıradaki adımlar):
- Adaptive Correction Memory (kullanıcı düzeltmelerinden öğrenme, §7)
- `update_event` gerçek implementasyonu (şu an sadece "henüz desteklemiyorum" mesajı)
- Web UI (her şey CLI — `python -m src.services.vertical_prototype` / `scan_inbox`)
- Formal pytest test suite (`tests/` klasörü var ama boş; şimdiye kadar tüm doğrulama scripted manuel testlerle yapıldı)
- Çoklu hesap desteği (tek hesap hardcoded: `src/connectors/account_registry.py` içinde `ACCOUNT_ID`/`ACCOUNT_EMAIL`)

## Canlı testte öğrenilen kritik teknik gerçekler

Bunlar tahmin değil, gerçek testlerle doğrulanmış bulgular — yeniden keşfetmeye çalışma:

**Foundry Local SDK (v1.2.4) gerçek API'si**, kamuya açık dokümantasyon/blog yazılarının tarif ettiği (HTTP sunucu) modelden **farklı**: native bir `.so`/`.dll` çekirdeği üzerinden in-process çalışıyor.
```python
Configuration(app_name=...) -> FoundryLocalManager.initialize(config)  # gerçek singleton
manager.catalog.get_model(alias) -> IModel
model.download(); model.load()
model.get_chat_client().complete_chat(messages)       # OpenAI-şekilli ChatCompletion döner
model.get_embedding_client().generate_embeddings(list) # OpenAI-şekilli embedding döner
```
Detaylar için `src/providers/foundry_local.py`'nin başındaki not.

**Model seçimi (ölçülmüş, keyfi değil):**
- Chat: `qwen3-4b`. Daha küçükler (`qwen3-0.6b`, `qwen3-1.7b`) Türkçe üretimde tutarsız/döngüye giren çıktılar verdi.
- Embedding: `qwen3-embedding-0.6b` (1024 boyut). TR/EN cross-lingual cosine similarity testte 0.77 çıktı — makul.
- Qwen3 ailesi "reasoning" modeli — varsayılan olarak uzun `<think>` blokları üretir. `/no_think` direktifi + `response_format=json_object` ile extraction görevlerinde gecikme **37sn → 5sn**'e indi (bkz. `FoundryLocalProvider.generate`).
- GPU/CUDA: sistemde gerçek NVIDIA GPU + CUDA runtime var ama `discover_eps()` boş liste döndürüyor, GPU hızlandırma **çözülemedi** — şu an her şey CPU'da çalışıyor. Sonraki bir oturumda tekrar denenebilir.

**LLM JSON çıktısı güvenilmez, üç ayrı bozulma modu gözlemlendi ve `src/providers/json_generation.py`'de savunulmuş durumda:**
1. Çıktı bazen markdown kod bloğuna sarılı (` ```json ... ``` `) — temizleniyor.
2. Bazen enum alanına (örn. `event_type`) seçilecek değer yerine seçenek listesinin tamamı yapıştırılıyor — güvenli varsayılana düşülüyor (`src/services/extraction.py::_coerce_event_type`, `vertical_prototype.py::VALID_IMPORTANCE_VALUES`).
3. Bazen tamamen boş/geçersiz çıktı (`Operation was cancelled` dahil) — 2 denemeye kadar retry yapılıyor.

**Sayısal alanlarda `0` ile `None` karışıklığına dikkat:** Model bazen `duration_minutes: 0` döndürüyor (null değil). Kodda bir yerde `is not None`, başka bir yerde `not x` kontrolü kullanmak gerçek bir bug'a yol açtı (0 bir yerde "dolu" bir yerde "eksik" sayıldı, kullanıcı doğru cevap verse bile candidate sessizce atlanıyordu — düzeltildi, ama bu desene yeni kod eklerken dikkat et).

**Google OAuth:** Gmail + Calendar için **tek, birleşik** scope seti kullanılıyor (`src/connectors/google_auth.py::GOOGLE_ACCOUNT_SCOPES`) — ayrı ayrı istenirse ikinci connector "yetersiz izin" hatası alır. `calendar.events` scope'u tek başına `freebusy.query` için yetersiz, `calendar.readonly` da gerekiyor (canlı testte 403 ile doğrulandı). Testing modundaki uygulamalarda refresh token 7 gün sonra geçersiz oluyor — `get_google_credentials` bunu yakalayıp otomatik yeniden interaktif yetkilendirmeye düşüyor.

**Mail sınıflandırma (takvimlik mi/değil mi):** Gmail'in kendi `CATEGORY_PROMOTIONS`/`CATEGORY_SOCIAL` etiketleri (`UnifiedEmail.labels`, `email_messages.labels` sütunu) LLM'e sormadan önce deterministik bir ön filtre olarak kullanılıyor — bu, promptla çözülemeyen tekrarlayan yanlış pozitifleri (kurs reklamları vb.) güvenilir şekilde eledi. **Ama** bu etiket her promosyon/bülten içerikte yok (Substack bültenleri, sipariş onayları gibi Gmail'in "Promotions" saymadığı içerikler) — bu durumlarda saf LLM sınıflandırmasına kalıyor ve **gerçek, kalıcı bir yanlış pozitif oranı var** (~%25-30, son ölçümde). İnsan onayı bunun güvenlik ağı; ileri bir iyileştirme olarak "thinking" modunu sınıflandırma için açmak veya Adaptive Correction Memory'ye bırakmak konuşuldu ama karar verilmedi.

## Veri saklama ilkeleri (uygulanmış, sadece plan değil)

- Mail gövdesi kalıcı saklanmıyor — `email_messages.body_excerpt` en fazla 2000 karakter (bkz. `BODY_EXCERPT_MAX_CHARS`).
- OAuth token'ları `data/` altında (git'e dahil değil), `chmod 600` (Windows'ta no-op ama zararsız).
- SQLite tek dosya: `data/calendar_agent.db`. Şema `src/storage/schema.sql`; `src/storage/db.py::init_db()` idempotent + geriye dönük sütun ekleyen küçük bir ad-hoc migration mekanizması içeriyor (tam bir migration framework değil, MVP için yeterli — yeni bir sütun eklerken `_ADHOC_COLUMN_MIGRATIONS` listesine ekle).

## Nasıl çalıştırılır / test edilir

```bash
# Konuşarak etkinlik oluşturma / takvim sorgulama / kural tanımlama
python -m src.services.vertical_prototype

# Gelen kutusunu tarama
python -m src.services.scan_inbox
```

Kurulum adımları için [README.md](README.md). Resmi bir test suite yok; bugüne kadarki tüm doğrulama şu şekilde yapıldı: (a) küçük scripted Python check'leri (syntax/import/pyflakes + hedefli fonksiyon çağrıları), (b) kullanıcının kendi interaktif oturumunda gerçek Gmail/Calendar hesabıyla canlı test. Bir şey bozulduğunda önce `data/debug.log`'a bak.

## Bu projede nasıl çalışılır (workflow tercihleri)

- **Küçük adımlarla ilerle, her adımdan sonra raporla.** Birçok değişikliği sessizce art arda yapıp sonunda özetleme.
- **Yalnızca bir şey gerçekten stabil çalışınca commit at** — bir hata ayıklama oturumundaki her ara düzeltme kendi commit'ini almasın; birden fazla düzeltme birlikte doğrulanıp tek commit'te toplanabilir.
- **Commit mesajları kısa, `Co-Authored-By: Claude` satırı olmadan** (kullanıcının açık tercihi).
- **İnteraktif/canlı testleri (gerçek takvime yazma, gerçek mail tarama gibi) kullanıcının kendisi çalıştırmayı tercih ediyor.** Kod yaz, syntax/import/pyflakes kontrolü ve mümkünse non-interactive scripted bir doğrulama yap, ama `vertical_prototype.py`/`scan_inbox.py`'yi uçtan uca sen çalıştırıp onaylama adımlarını simüle etmeye çalışma — bu kullanıcının işi.
- Gerçek takvime yazma, gerçek kural ekleme gibi kalıcı/geri alınamaz aksiyonları dikkatli değerlendir (bkz. genel Claude Code güvenlik ilkeleri) — bu proje özelinde onay akışı zaten var, onu bypass etme.

## Repo yapısı

```
src/
  core/          # Pydantic domain modelleri + logging_config.py
  providers/     # LLMProvider/EmbeddingProvider soyutlaması, FoundryLocalProvider,
                 # json_generation.py (JSON-çıktı retry+temizleme ortak yardımcısı)
  connectors/    # Gmail, Google Calendar, OAuth (google_auth.py), Account Registry
  services/      # intent.py, extraction.py (ortak alan eşleme), timeutil.py,
                 # availability.py (Conflict Engine), mail_sync.py, mail_analysis.py,
                 # vertical_prototype.py (ana konuşma akışı + review_and_confirm_candidate
                 # — hem konuşma hem mail akışının paylaştığı ortak onay/yazma fonksiyonu),
                 # scan_inbox.py
  policies/      # Policy Store (CRUD)
  rag/           # Embedding tabanlı policy retrieval
  storage/       # db.py (bağlantı+migration), schema.sql
tests/           # Şu an boş — resmi test suite yazılmadı
data/            # SQLite DB, OAuth dosyaları, debug.log — git'e dahil DEĞİL
docs/            # architecture-plan.md (tam mimari) + program dokümanı PDF'i
```
