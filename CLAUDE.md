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
- Çoklu hesap desteği (CLI seviyesinde): `src/connectors/account_registry.py::select_account()` her çalıştırmada kayıtlı hesaplardan seçtiriyor veya yeni hesap ekletip OAuth'u tetikliyor; alt katman (connector'lar, `mail_sync.py`, `sync_states`/`email_messages`) zaten `account_id` parametreliydi, tek eksik CLI'daki sabit kodlanmış `ACCOUNT_ID`/`ACCOUNT_EMAIL`'di — canlı testte iki gerçek Gmail hesabıyla (ayrı ayrı OAuth + etkinlik ekleme) doğrulandı.
- GPU (CUDA) hızlandırma: `FoundryLocalProvider`/`FoundryLocalEmbeddingProvider` artık `prefer_gpu=True` varsayılanıyla CUDA execution provider'ı deneyip modelin `-cuda-gpu` varyantını kullanıyor, başarısız olursa (GPU yok/offline/VRAM yetersiz) sessizce CPU'ya düşüyor — bkz. aşağıdaki "GPU/CUDA" notu.
- Adaptive Correction Memory (§10) — canlı test edildi (reddet→düzelt→kural akışı, düzenle→kural akışı, sender-scope, mail sınıflandırma düzeltmesi hepsi kullanıcının kendi testinde çalıştı):
  - **Reddetme akışı:** `review_and_confirm_candidate`'in son `[e/h/d]` onayında `h` ile reddedilince "neden reddettiniz?" sorup `user_corrections`'a kaydediyor (`src/memory/correction_memory.py::capture_correction_interactively`); "gelecekte de uygulayayım mı?" onayı alınırsa event_type / sender (yalnızca mail kaynaklı candidate'larda) / global scope seçtirip `src/policies/derivation.py::derive_and_save_policy` ile bir `PersonalPolicy` türetiyor.
  - **Düzenleme akışı:** `d` ile önizlemede süre/önem/başlık/saat/konum düzenlenebiliyor (`edit_candidate_field_interactively`); süre/önem düzenlenip sonra onaylanırsa `capture_edit_correction` LLM'siz, doğrudan (`save_derived_policy`) bir kural teklif ediyor — kullanıcı zaten net değer yazdığı için tahmine gerek yok. `UserCorrection.original_output`/`corrected_output` artık gerçek bir önce/sonra farkı taşıyor.
  - **Sender-scope:** `PersonalPolicy` sender-scope'lu olabiliyor (`src/policies/store.py::normalize_sender`, `get_active_policies_for_sender`); `retrieve_policies_for_event` sender eşleşmesini deterministik yapıyor ve semantik havuzdan sender-scope'lu politikaları ÇIKARIYOR (canlı testte gerçek bir sızıntı bulundu: sender-scope'lu bir politika, filtrelenmeden önce ilgisiz göndericilere de semantik benzerlikle sızabiliyordu).
  - **Mail sınıflandırma düzeltmesi:** reddederken "bu mail hiç takvimlik değil miydi, yoksa bilgiler mi yanlıştı?" diye ayrıca soruluyor; ilki seçilirse `save_classification_correction` mail metnini embed edip `correction_embeddings`'e (`user_corrections.correction_type='classification'`) yazıyor — `src/services/mail_analysis.py::is_calendar_worthy` artık `embedding_provider` alıyor ve `src/rag/correction_retrieval.py::retrieve_similar_classification_corrections` ile geçmiş benzer düzeltmeleri `context_chunks` olarak LLM'e veriyor (RAG burada da karar vermiyor, sadece bağlam sağlıyor — §11).
  - `add_policy`/policy tanımlama çelişki tespiti + versiyonlama yapıyor (aynı category+kapsamda zaten aktif bir politika varsa eskisi pasifleştirilip `policy_versions`'a snapshot'lanıyor, silinmiyor) — bu, manuel kural tanımlamayı da (`handle_define_policy`) kapsıyor.
  - Kapsam dışı bırakılan: account-scope (kullanılmıyor, tek kullanıcı için sender/global yeterli).
- `update_event` (yalnızca konuşma akışından): `handle_update_event` kullanıcı mesajından `title_hint`/`date_hint`/`cancel`/yeni saat çıkarıp Google Calendar'da CANLI arama yapıyor (`calendar_events_cache` kullanılmıyor, hiç doldurulmuyor) — sıfır/çoklu eşleşmede tahmin etmeden soruyor, tek eşleşmede önizleme+onay sonrası `update_event`/yeni `delete_event` (connector'a eklendi) çağırıyor. Mail kaynaklı güncelleme tespiti (plan §8.3, thread/semantic ilişkilendirmeye bağımlı) kapsam dışı.
- Formal pytest test suite (`tests/`) — ilk dilim: `timeutil.py`, `availability.py`, `extraction.py`, `policies/store.py`, `_find_matching_events` (update_event eşleştirme mantığı), `candidates/store.py` için deterministik testler, gerçek DB'ye dokunmayan izole `temp_db` fixture'ıyla. LLM/embedding'e bağımlı testler ve connector mock testleri henüz yok.
- **Web UI — ilk dilim: "Gelen Öneriler"** (`src/ui/`, FastAPI+Jinja2, `python -m src.ui.app`, `http://127.0.0.1:8000/oneriler`). Yalnızca **mail kaynaklı** candidate'lar (konuşma kaynaklı candidate'ların account_id bağlantısı yok). **Bu, `scan_inbox.py`'nin davranışını kalıcı olarak değiştirdi:** artık interaktif onaylatmıyor, her takvimlik maili doğrudan `candidate_events`'e (`src/candidates/store.py::save_new_candidate`) yazıp geçiyor — onay SADECE web'den. `review_and_confirm_candidate` artık yalnızca `vertical_prototype.py` (konuşma akışı) tarafından kullanılıyor. Web'de onayla (çakışma varsa "yine de ekle" onayı ister) / reddet (opsiyonel sebep, ham düzeltme olarak `save_user_correction` ile kaydedilir) / düzenle (eksik/yanlış alanlar) var. ACM'nin "gelecekte de uygulayayım mı?" (politika türetme, scope seçimi) akışı web'de YOK — bilinçli olarak kapsam dışı, CLI'da kalmaya devam ediyor. Web sunucusu login/auth içermiyor (tek kullanıcı, yerel-öncelikli); bağlı hesapların OAuth token'larının CLI üzerinden en az bir kez oluşturulmuş olması gerekiyor.
- **Web UI — ikinci dilim: tam sol navigasyon + "E-posta Hesapları"** (`src/ui/routes.py`, `src/ui/templates/base.html`). Sol nav (§16: Ana Sayfa/Takvim/Gelen Öneriler/E-posta Hesapları/Kurallarım/Düzeltmelerim/Ayarlar) artık tüm sayfalarda görünüyor, `active_page` context değişkeniyle vurgulanıyor. `scan_inbox.py`'nin çekirdek for-loop'u `scan_account_inbox(account_id, llm, embedding_provider, on_progress=None) -> dict` olarak çıkarıldı — hem CLI'nın `main()`'i hem web'in "Şimdi tara" butonu (`POST /hesaplar/{account_id}/tara`) aynı fonksiyonu çağırıyor, CLI ilerleme metni için `on_progress=print` veriyor, web sessiz (senkron/bloklayan istek, arka plan kuyruğu yok). `GET /hesaplar` bağlı hesapları (`account_registry.py::list_accounts()`, artık `connected_at` da dönüyor) kart olarak listeliyor. Ana Sayfa/Takvim/Kurallarım/Düzeltmelerim/Ayarlar tek bir `GET /{page_name}` catch-all route'una (`yakinda.html` placeholder, `PLACEHOLDER_PAGES` sözlüğünde izin verilen sayfa adları) gidiyor — **bu route dosyanın en sonunda tanımlı olmalı**, Starlette route'ları kayıt sırasına göre eşleştirdiğinden daha önce tanımlanırsa `/hesaplar` gibi spesifik route'ları gölgeler. `tests/test_ui_routes.py`: `FoundryLocalProvider`/`FoundryLocalEmbeddingProvider`'ı sahte bir sınıfla monkeypatch'leyip (`src.ui.app.FoundryLocalProvider` vb.) gerçek model yüklemeden `TestClient` ile nav/placeholder/hesaplar route'larını doğruluyor. Yeni hesap ekleme/tarayıcıda OAuth başlatma ve chatbox (Ana Sayfa'nın konuşma akışı) hâlâ kapsam dışı — sıradaki dilim chatbox.
- Loglama sistemi: her LLM çağrısı + karar noktası `data/debug.log`'a yazılıyor (`src/core/logging_config.py`) — **bir şey beklenmedik davranırsa önce buraya bak, tahmin etmeye çalışma.**

Henüz yok (plan §19/§25'e göre sıradaki adımlar):
- Web UI'de chatbox (Ana Sayfa'nın konuşma akışı — sohbet/create_event/query_calendar/define_policy'nin webde çalışması, oturum durumu gerektiriyor); Takvim/Kurallarım/Düzeltmelerim/Ayarlar'ın gerçek içeriği (hâlâ "yakında" placeholder); web'de ACM'nin politika türetme akışı; web'den `update_event`; web'den yeni hesap ekleme (tarayıcıda OAuth).
- Mail kaynaklı `update_event` tespiti (plan §8.3, thread/semantic ilişkilendirmeye bağımlı).
- `calendar_events_cache` senkronizasyonu (şemada var, hiç doldurulmuyor).

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
- GPU/CUDA (**çözüldü**, 2026-08-16 — önceki "discover_eps() boş liste döndürüyor" bulgusu güncelliğini yitirdi, muhtemelen sürücü/SDK güncellemesiyle değişti): `manager.discover_eps()` artık `CUDAExecutionProvider`'ı listeliyor; `manager.download_and_register_eps(names=["CUDAExecutionProvider"])` ile kaydedilince katalog o model için ayrı bir `<alias>-cuda-gpu` varyantı sunuyor. Ölçüm: qwen3-4b'de chat generation CPU'da ~5-20sn/çağrı iken GPU'da (soğuk yükleme sonrası) **~0.1-0.5sn/çağrı**. İki kritik kısıt: (1) EP kaydı process başına ~45-90sn sürüyor ve **hiçbir yerde persist olmuyor** — her process yeniden başladığında tekrarlanıyor (`foundry_local.py::_ensure_gpu_registered`, süreç içi önbelleğe alınıyor ki chat+embedding provider'ı aynı process'te ikinci kez tetiklemesin); (2) GPU varyantı CPU varyantından **farklı bir model id'si** (örn. `qwen3-4b-cuda-gpu:2` vs `qwen3-4b-generic-cpu:3`), yani ilk kullanımda ayrıca indiriliyor (~2.7GB chat + ~0.5GB embedding, CPU kopyalarının YANINDA, üzerine yazmıyor). Embedding tarafı için ayrıca önemli: `EmbeddingProvider.model_name` bu id'yi döndürüyor ve `policy_embeddings` gibi tablolarda satır başına saklanıyor — `rag/policy_retrieval.py::semantic_search_policies` bunu artık WHERE'de filtreliyor (önceden filtrelemiyordu, CPU/GPU karışık embedding'leri sessizce yanlış karşılaştırabilirdi), yani model değişince eski embedding'ler retrieval'dan sessizce düşer, yeniden embed edilmesi gerekir.

**LLM JSON çıktısı güvenilmez, üç ayrı bozulma modu gözlemlendi ve `src/providers/json_generation.py`'de savunulmuş durumda:**
1. Çıktı bazen markdown kod bloğuna sarılı (` ```json ... ``` `) — temizleniyor.
2. Bazen enum alanına (örn. `event_type`) seçilecek değer yerine seçenek listesinin tamamı yapıştırılıyor — güvenli varsayılana düşülüyor (`src/services/extraction.py::_coerce_event_type`, `vertical_prototype.py::VALID_IMPORTANCE_VALUES`).
3. Bazen tamamen boş/geçersiz çıktı (`Operation was cancelled` dahil) — 2 denemeye kadar retry yapılıyor.

**Sayısal alanlarda `0` ile `None` karışıklığına dikkat:** Model bazen `duration_minutes: 0` döndürüyor (null değil). Kodda bir yerde `is not None`, başka bir yerde `not x` kontrolü kullanmak gerçek bir bug'a yol açtı (0 bir yerde "dolu" bir yerde "eksik" sayıldı, kullanıcı doğru cevap verse bile candidate sessizce atlanıyordu — düzeltildi, ama bu desene yeni kod eklerken dikkat et).

**Google OAuth:** Gmail + Calendar için **tek, birleşik** scope seti kullanılıyor (`src/connectors/google_auth.py::GOOGLE_ACCOUNT_SCOPES`) — ayrı ayrı istenirse ikinci connector "yetersiz izin" hatası alır. `calendar.events` scope'u tek başına `freebusy.query` için yetersiz, `calendar.readonly` da gerekiyor (canlı testte 403 ile doğrulandı). Testing modundaki uygulamalarda refresh token 7 gün sonra geçersiz oluyor — `get_google_credentials` bunu yakalayıp otomatik yeniden interaktif yetkilendirmeye düşüyor.

**Mail sınıflandırma (takvimlik mi/değil mi):** Gmail'in kendi `CATEGORY_PROMOTIONS`/`CATEGORY_SOCIAL` etiketleri (`UnifiedEmail.labels`, `email_messages.labels` sütunu) LLM'e sormadan önce deterministik bir ön filtre olarak kullanılıyor — bu, promptla çözülemeyen tekrarlayan yanlış pozitifleri (kurs reklamları vb.) güvenilir şekilde eledi. **Ama** bu etiket her promosyon/bülten içerikte yok (Substack bültenleri, LinkedIn iş ilanı uyarıları gibi Gmail'in "Promotions" saymadığı içerikler) — bu durumlarda saf LLM sınıflandırmasına kalıyor.

**Sınıflandırma için thinking modu açık (çözüldü, 2026-08-16):** `/no_think` ile (hızlı ama) tarih/saat içermeyen iş ilanlarını ("X şirketinde Y pozisyonu") takvimlik sanma gibi yanlış pozitifler kalıcıydı — canlı testte `allow_thinking=False` ile aynı promptun aynı maili yanlış sınıflandırdığı, `allow_thinking=True` ile (aynı prompt, aynı model) doğru sınıflandırdığı doğrulandı. GPU sayesinde thinking'in maliyeti artık tolere edilebilir (~3-7sn/mail, eskiden CPU'da 37sn'lik "hızlandırma öncesi" değere yakın ama tek seferlik bir maliyet — extraction hâlâ `/no_think` ile hızlı kalıyor, sadece `is_calendar_worthy` çağrısı `generate_json(..., allow_thinking=True)` kullanıyor, bkz. `mail_analysis.py`). `LLMProvider.generate()`/`generate_json()` artık bir `allow_thinking` parametresi alıyor (varsayılan `False`, geriye dönük uyumlu). Kalan yanlış pozitif oranı ölçülmedi (önceki ~%25-30 rakamı `allow_thinking=False` dönemine ait, güncellenmesi gerekiyor) — insan onayı yine de güvenlik ağı.

## Veri saklama ilkeleri (uygulanmış, sadece plan değil)

- Mail gövdesi kalıcı saklanmıyor — `email_messages.body_excerpt` en fazla 2000 karakter (bkz. `BODY_EXCERPT_MAX_CHARS`).
- OAuth token'ları `data/` altında (git'e dahil değil), `chmod 600` (Windows'ta no-op ama zararsız).
- SQLite tek dosya: `data/calendar_agent.db`. Şema `src/storage/schema.sql`; `src/storage/db.py::init_db()` idempotent + geriye dönük sütun ekleyen küçük bir ad-hoc migration mekanizması içeriyor (tam bir migration framework değil, MVP için yeterli — yeni bir sütun eklerken `_ADHOC_COLUMN_MIGRATIONS` listesine ekle).

## Nasıl çalıştırılır / test edilir

```bash
# Konuşarak etkinlik oluşturma / takvim sorgulama / kural tanımlama / update_event
python -m src.services.vertical_prototype

# Gelen kutusunu tarama (artık yalnızca kuyruğa yazıyor, onay web'den)
python -m src.services.scan_inbox

# Web UI ("Gelen Öneriler") — http://127.0.0.1:8000/oneriler
python -m src.ui.app

# Testler
python -m pytest tests/
```

Kurulum adımları için [README.md](README.md). `tests/`'te ilk pytest dilimi var (deterministik mantık — bkz. yukarıdaki "Güncel durum"), ama asıl doğrulama hâlâ şu şekilde: (a) küçük scripted Python check'leri (syntax/import/pyflakes + hedefli fonksiyon çağrıları), (b) kullanıcının kendi interaktif oturumunda gerçek Gmail/Calendar hesabıyla canlı test. Bir şey bozulduğunda önce `data/debug.log`'a bak.

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
                 # vertical_prototype.py (konuşma akışı: create/query/update_event/
                 # define_policy + review_and_confirm_candidate — yalnızca konuşma
                 # akışının kullandığı interaktif onay/yazma fonksiyonu), scan_inbox.py
                 # (artık interaktif değil, yalnızca kuyruğa yazıyor — bkz. candidates/)
  candidates/    # store.py (Candidate Event Queue persistence: save_new_candidate,
                 # list/get_pending_candidate, update_candidate_fields — Web UI ve
                 # scan_inbox.py'nin ortak kullandığı, self-contained repository)
  policies/      # store.py (CRUD + çelişki tespiti/versiyonlama), derivation.py
                 # (doğal dil kural -> PersonalPolicy, manuel + ACM ortak)
  memory/        # correction_memory.py (Adaptive Correction Memory: düzeltme
                 # yakalama, onay akışı, politika türetmeyi derivation.py'ye devreder)
  rag/           # policy_retrieval.py (policy embedding/retrieval),
                 # correction_retrieval.py (mail sınıflandırma düzeltmeleri —
                 # ayrı tablo/amaç, policy_retrieval'a kasıtlı olarak karıştırılmıyor)
  storage/       # db.py (bağlantı+migration), schema.sql
  ui/            # app.py (FastAPI + lifespan'de tek seferlik LLM/embedding yükleme),
                 # routes.py (sol nav + "Gelen Öneriler" + "E-posta Hesapları"), templates/, static/
tests/           # timeutil/availability/extraction/policies/candidates store'ları
                 # için deterministik testler + test_ui_routes.py (TestClient,
                 # FoundryLocal* monkeypatch'li) — hepsi temp_db fixture'ıyla izole
data/            # SQLite DB, OAuth dosyaları, debug.log — git'e dahil DEĞİL
docs/            # architecture-plan.md (tam mimari) + program dokümanı PDF'i
```
