**Geliştirme durumu — 10 Eylül 2026**

Ana plan: [Ürünleşme ve tasarım yol haritası](urunlesme-ve-tasarim-yol-haritasi.md).

Çalışma düzeni: her kontrol noktasında kod ve otomatik doğrulama tamamlanır, kullanıcıya manuel test adımları verilir. Sonraki geliştirme adımına kullanıcı onayından sonra geçilir.

**Kontrol noktası 1 — AUTH-01: öneri erişim kontrolü**

Durum: kullanıcı tarafından onaylandı. Kod ve testler `9713386` (`Fix suggestion access control`) commit’iyle kaydedildi; co-author satırı yok.

Önerinin kaynağı olan hesabın veritabanındaki güncel sahibi kontrol ediliyor. Kontrol, görüntüleme GET isteğinde ve düzenleme/onaylama/reddetme POST isteklerinde ortak bir yardımcı üzerinden çalışıyor. Çakışmayı zorlayarak onaylama ve güncelleme önerisi işlemleri de aynı kontrolden geçiyor. Düzenleme store çağrısı ayrıca kullanıcı kapsamı alıyor. Yerel servislerin mevcut kapsamsız store kullanımı korunuyor; web rotaları oturum kullanıcısını geçiriyor.

Erişilemeyen, sahipsiz veya artık bekleyen durumda olmayan kayıtlar doğrudan işlem rotalarında 404 döndürüyor. Başkasının verisi ile var olmayan ID aynı yanıtı veriyor. Kayıt sahibi kendi önerisini düzenleyebiliyor/onaylayabiliyor/reddedebiliyor; kendisine ait başka bir hesabı hedef takvim seçme davranışı korunuyor.

Değişen dosyalar:

- [store.py](../src/candidates/store.py): kullanıcı kapsamlı öneri sorgusu ve düzenleme.
- [routes.py](../src/ui/routes.py): dört işlem rotasında ortak erişim kontrolü.
- [test_candidate_authorization.py](../tests/test_candidate_authorization.py): 30 yeni test; yetkisiz erişim, yan etki oluşmaması, zorla onay, güncelleme/geri alma, oturumsuz erişim ve hesap devrinden sonra açık kalan form.
- [test_ui_routes.py](../tests/test_ui_routes.py): mevcut başarılı işlem testlerinde hesapların gerçek bir kullanıcı sahibine bağlanması.

Doğrulama: ilk regresyon çalıştırmasında yeni 28 senaryonun 18’i mevcut davranışla başarısız oldu. Düzeltme ve iki ek store testi sonrası tüm test paketi **508 başarılı, 13 deprecation uyarısı** ile tamamlandı. Tam paket, önceki raporda saptanan test izolasyonu eksikliği nedeniyle auth modüllerinin `DATA_DIR` değerleri yalnızca süreç boyunca geçici klasöre yönlendirilerek çalıştırıldı. Kalıcı test izolasyonu henüz ayrı bir iş. Değiştirilen Python dosyaları için pyflakes ve diff boşluk kontrolü geçti.

**Senin yapacağın manuel kontrol**

1. Çalışan uygulamayı durdurup yeniden başlat; mevcut komut otomatik yeniden yükleme kullanmıyor:

   ```powershell
   .venv\Scripts\python.exe -m src.ui.app
   ```

2. `http://127.0.0.1:8000` adresinde giriş yap. Önerilerden sana ait, deneme amaçlı bir kaydın **Düzenle** sayfasını aç. Başlığı değiştirip kaydet. Listede yeni başlık görünmeli.
3. Aynı kaydın `/oneriler/<id>/duzenle` bağlantısını kopyala. Gizli pencerede **ayrı bir kullanıcı olarak** farklı hesapla giriş yapıp bu bağlantıyı aç. 404 sayfası görünmeli; ilk kullanıcının önerisi görünmemeli. Aynı kullanıcıya bağlı hesaplar arasında seçim yapmak bu senaryoyu test etmez; ikinci kullanıcıya ilk kullanıcının kaynak hesabını bağlama.
4. İlk pencereye dön. Öneri yerinde kalmalı ve yalnızca senin kaydettiğin değişikliği taşımalı.
5. Oturumsuz gizli pencerede aynı bağlantı giriş sayfasına yönlenmeli.

İki bağımsız giriş hesabın yoksa 2. adımı kontrol etmen yeterli ilk manuel doğrulama olur; çapraz kullanıcı GET/POST senaryoları otomatik testlerde geçiyor. Gerçek takvime yazma bu çalışmada denenmedi. İstersen yalnızca deneme amaçlı bir öneriyi onaylayarak normal onay akışını da kontrol edebilirsin; bu adım gerçek takvimine etkinlik ekler.

Yeni güvenlik testlerini tek başına çalıştırmak için:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_candidate_authorization.py -q
```

**Bu kontrol noktasının sınırı ve sıradaki iş**

Bu adım önerilerin doğrudan işlem rotalarını korur. Eski sahipsiz hesapların genel listelerde/bildirimlerde gösterilmesi ve girişte devralınması henüz değiştirilmedi; bu davranış yol haritasındaki AUTH-02 adımıdır. Bu nedenle yalnızca bu değişiklikle tüm uygulamanın çok kullanıcılı yayına hazır olduğu sonucuna varılmamalı. Sahipsiz eski bir öneri listede görünse de düzenleme/onay rotasında 404 alabilir; sonraki adımda görünürlük ve sahiplik taşıma davranışı birlikte ele alınacak.

Bu ilk kontrol noktasından sonra AUTH-02 çalışmasına geçildi; güncel durum aşağıdadır.

**Kontrol noktası 2 — sahipsiz verilerin web görünürlüğü ve otomatik devri**

Durum: kullanıcı tarafından onaylandı. Kod ve testler `fdd8d3a` (`Isolate unowned account data`) commit’iyle kaydedildi; co-author satırı yok.

Önceden yeni giriş yapan kullanıcı, sahipliği belirlenmemiş tüm eski hesapları, tercihleri, kuralları ve düzeltmeleri topluca devralabiliyordu. Sahipsiz hesaplar diğer kullanıcıların hesap listesi ve bildirimlerinde de görünebiliyordu.

Yeni davranış:

- Web hesap listesi yalnızca oturum kullanıcısına ait hesapları döndürüyor. Hesap seçimi, ana sayfa, öneriler ve ortak bildirim alanı bu kapsama bağlı.
- Sahipsiz bir hesap, doğrudan seçim isteğiyle veya eski `active_account` çereziyle etkinleştirilemiyor.
- Google ve Outlook girişinde toplu eski veri devri kaldırıldı. Mevcut OAuth ile tek hesabı bağlama davranışı korunuyor.
- Eski kayıtlar silinmedi veya gerçek veritabanında topluca taşınmadı. Sahibi zaten atanmış hesaplar etkilenmiyor.
- Yerel CLI kapsam verilmeden tüm hesapları okuyabiliyor. Toplu veri taşıma yardımcısı yalnızca açıkça istenen yerel taşıma için korundu; web tarafından çağrılmıyor.

AUTH-02’nin hesap kimliklerinin çakışmaya dayanıklı hale getirilmesi ve eski kimliklerin güvenli taşınması bölümü henüz tamamlanmadı. Bu kontrol noktası yalnızca sahipsiz verilerin görünürlüğü ve otomatik sahiplik devrini kapsıyor. Kaynak hesap ilişkisi bulunmayan eski kişisel kurallar/tercihler OAuth ile otomatik geri yüklenmez; ayrıca sahiplik eşlemesi gerektirir.

Doğrulama: [test_account_isolation.py](../tests/test_account_isolation.py) içinde 12 yeni senaryo. Düzeltmeden önce bunların 10’u başarısızdı; sonrasında geçti. Yeni hesap listesi kurallarına uygun olarak mevcut arayüz/sohbet test verilerine açık kullanıcı sahipliği eklendi. Tüm testler geçici token dizinleriyle **520 başarılı, 13 deprecation uyarısı**; pyflakes ve diff kontrolü temiz. Gerçek sağlayıcılarda yeniden giriş bu çalışmada denenmedi.

Manuel kontrol:

1. Uygulamayı durdurup `.venv\Scripts\python.exe -m src.ui.app` ile yeniden başlat.
2. Normal hesabınla giriş yap. E-posta Hesapları sayfasında kendi bağlı hesaplarını görebildiğini kontrol et.
3. Birden fazla hesabın varsa aralarında geçiş yap; öneriler ve asistan açılmalı. Kendi bir önerinin düzenleme sayfası erişilebilir kalmalı.
4. Daha önce sahipsiz bir bağlantı görünürken şimdi kaybolduysa bu silindiği anlamına gelmez. O hesabı normal Google/Outlook bağlama akışıyla yeniden bağlayıp kontrol et. Beklediğin kayıt geri gelmezse sonucu bildir; toplu veri taşıması yapma.
5. Ayrı bir kullanıcıyla deneme imkânın varsa ilk kullanıcının hesapları ve bildirimleri görünmemeli. Bu ayrım otomatik testlerde de doğrulandı.

Bu adım onaylandıktan sonra hesap kimliği eşlemesine geçildi; güncel durum aşağıdadır.

**Kontrol noktası 3 — hesap kimliklerinin çakışmasını önleme ve eski bağlantıları koruma**

Durum: kullanıcı tarafından onaylandı. Kod ve testler `051a071` (`Prevent account identity collisions`) commit’iyle kaydedildi; co-author satırı yok.

Önceden `ali@firma-a.com` ve `ali@firma-b.com`, adresin yalnızca `@` öncesi kısmından türetilen aynı kimliği alabiliyordu. Mevcut ID’nin sağlayıcı/adres kontrolü yapılmadan yeniden kullanılması, başka hesabın sahipliğinin ve token dosyasının değişmesine yol açabiliyordu.

Yeni davranış:

- Yeni kayıtlar sağlayıcı ve normalize edilmiş tam e-posta adresinden türetilen kimliklerle ayrılıyor. Google ve Outlook aynı adresle kullanılsa bile ayrı hesap kayıtları oluyor.
- `register_account` önce aynı sağlayıcı/adrese ait mevcut kaydı arıyor. Tek eşleşme varsa eski ID aynen korunuyor; token dosyaları, öneri ilişkileri ve hedef takvim tercihi yeniden adlandırılmıyor.
- Kayıt eşleştirme ve oluşturma aynı SQLite yazma transaction’ında yapılıyor. Eşzamanlı aynı hesap kaydı testinde tek kayıt oluşuyor.
- Doğrudan eski ID ile kayıt çağrısında sağlayıcı/adres uyuşmazsa sahiplik değişmeden hata veriliyor.
- Bir adres için birden fazla eski kayıt varsa otomatik birleştirme yapılmıyor. OAuth akışı hata gösteriyor ve mevcut token dosyalarına dokunmuyor.
- Google/Outlook OAuth ve yerel hesap ekleme ortak kayıt yordamını kullanıyor. Başarılı OAuth bağlantısında ilgili takvim connector cache’i yenilenmek üzere kaldırılıyor.

Doğrulama: [test_account_identity.py](../tests/test_account_identity.py) ve [test_oauth_account_identity.py](../tests/test_oauth_account_identity.py) ile 20 yeni test. Aynı adres öneki, farklı sağlayıcılar, eski özel ID, büyük/küçük harf farkı, eşzamanlı kayıt, kimlik uyuşmazlığı, token dosyalarının korunması ve cache yenilenmesi kapsandı. Tam paket geçici token dizinleriyle **540 başarılı, 13 deprecation uyarısı**; pyflakes ve diff kontrolü temiz. Gerçek hesaplarda OAuth veya veri taşıması yapılmadı.

Manuel kontrol:

1. Uygulamayı durdurup `.venv\Scripts\python.exe -m src.ui.app` ile yeniden başlat.
2. Kendi hesabınla giriş yap; mevcut hesapları ve bir önerinin düzenleme sayfasını kontrol et.
3. E-posta Hesapları ekranından zaten bağlı olan hesabını aynı kullanıcı oturumu içinde yeniden bağla. Hesabı önce silmen gerekmez. Listede ikinci bir kopya oluşmamalı; önceki önerilerin ve hedef takvim seçimin korunmalı.
4. Google ve Outlook bağlantıların varsa normal hesap geçişini dene. Benzer e-posta adresi senaryosu için yeni hesap açman gerekmiyor; ayrım otomatik testlerde doğrulandı.
5. Yeniden bağlama hata verirse sonucu bildir; mevcut kayıtları silip yeniden kurmaya çalışma. Eski mükerrer hesapların açıkça değerlendirilmesi gerekebilir.

Sınırlar: eşleştirme şu an sağlayıcı + tam e-posta üzerinden yapılıyor. Sağlayıcının değişmez kullanıcı kimliğine (`sub`/Graph ID) geçiş ve e-posta adresi değişikliğinin/alias’ların otomatik tanınması bu değişikliğin kapsamında değil. Token dosyası ile DB kaydı henüz tek atomik işlem oluşturmuyor; kayıt sonrası disk yazma hatasının kurtarma davranışı üretim güvenilirliği çalışmasında ele alınmalı. Daha önce yanlış hesaba bağlanmış veya mükerrer eski veriler otomatik onarılmadı.

Kullanıcı onayından sonra kalıcı test izolasyonuna geçildi; güncel durum aşağıdadır.

**Kontrol noktası 4 — TEST-01: kalıcı test izolasyonu**

Durum: kullanıcı tarafından onaylandı. Kod ve testler `95aea7e` commit'iyle kaydedildi.

Önceden standart `pytest tests/` komutu geliştirme makinesinde 11 testte başarısız oluyordu; başarısız sohbet rotası testleri gerçek Google OAuth token yenilemesine ulaşıyordu. Testlerin geçmesi için `DATA_DIR` değerlerini süreç boyunca elle geçici klasöre yönlendiren bir sarmalayıcı gerekiyordu. Bu, gerçek kimlik bilgilerinin test sürecine sızabildiği anlamına da geliyordu; `data/` dizinindeki `google_token_acc1.json` ve `google_token_newuser.json` gibi test kaynaklı dosyalar bu sızıntının izidir.

Yeni davranış — izolasyon sınırları test sürecinde kalıcı olarak kuruluyor:

- Ortam değişkenleri: `LLM_PROVIDER`, `GOOGLE_API_KEY`, `MS_CLIENT_ID` ve diğer sağlayıcı değişkenleri her testte siliniyor.
- `.env` okuması: `dotenv.load_dotenv` etkisiz kılınıyor. Bu `pytest_configure` içinde yapılıyor; `src/ui/app.py` `.env`’i içe aktarma anında okuduğu için fixture’lar tek başına geç kalıyordu.
- Dosya sistemi: OAuth token dizini, Google istemci dosyası, `debug.log` ve SQLite veritabanı teste özel geçici klasöre yönlendiriliyor. `temp_db` artık otomatik uygulanıyor; testin ayrıca istemesi gerekmiyor.
- Ağ: soket bağlantıları, DNS çözümlemesi ve UDP gönderimi engelleniyor. `socketpair` yalnızca kendi bağlamında serbest; bu olmadan Windows’ta asyncio ve `TestClient` çalışmıyor. Engel, gerçek `requests`/`httpx` çağrılarında da devrede.
- Yerel model: `foundry_local._get_manager` çağrısı testte açık bir hata veriyor, native model başlatılmıyor.
- `get_google_credentials` istemci dosyası yolunu artık çağrı anında çözüyor. Önceden varsayılan parametre içe aktarma anında bağlanıyordu ve yönlendirme bu yolu etkilemiyordu; davranış aynı, açık parametre geçmek hâlâ destekleniyor.

Sabitleri `from ... import` ile kendi ad alanına kopyalayan modüller ayrıca yamalanıyor: böyle bir kopya içe aktarma anında bağlandığı için sabiti tanımlayan modülü yamalamak yetmiyor. Bu liste `tests/_isolation.py` içinde bildiriliyor ve bir test bildirimi kaynak ağacıyla karşılaştırıyor. İleride bir modül `from src.core.logging_config import LOG_PATH` eklerse izolasyon sessizce delinmek yerine test başarısız oluyor; koruma testinin gerçekten kırıldığı, yapay bir sapma enjekte edilerek doğrulandı.

Değişen dosyalar:

- [_isolation.py](../tests/_isolation.py): ortam/ağ/model engelleri, kopyalanan sabit bildirimi ve kaynak tarayıcısı.
- [conftest.py](../tests/conftest.py): `pytest_configure` engelleri, otomatik `temp_db`, `isolated_files` fixture’ı.
- [test_test_isolation.py](../tests/test_test_isolation.py): 19 test; ortam, `.env`, geçici veritabanı, token/log yazımı, istemci yolu çözümü, TCP/DNS/UDP/HTTP engelleri, `socketpair`, asyncio, native model ve sapma koruması.
- [google_auth.py](../src/connectors/google_auth.py): istemci dosyası yolunun çağrı anında çözülmesi.
- [test_outlook_connector.py](../tests/test_outlook_connector.py): kalıcı modül değişikliği yerine `monkeypatch` kullanımı.
- [.gitignore](../.gitignore): `.claude` dizini yok sayılıyor (bu adımdan bağımsız küçük bir düzen).

Doğrulama — iki senaryo ayrı ayrı çalıştırıldı:

| Senaryo | Sonuç |
|---|---|
| Gerçek `.env` ve gerçek OAuth token’ları bulunan bu geliştirme makinesinde `pytest tests/` | **559 başarılı**, 13 deprecation uyarısı; sarmalayıcı gerekmedi. |
| Aynı çalıştırmadan sonra `data/` dizini | Dokuz dosyanın tamamı bayt bayt aynı (md5), yeni dosya oluşmadı. |
| `.env` ve `data/` bulunmayan temiz kopya | **559 başarılı**; `data/` dizini hiç oluşturulmadı. |

pyflakes değişen dosyalarda temiz. Gerçek sağlayıcılara istek gönderilmedi.

**Senin yapacağın manuel kontrol**

1. Testleri sarmalayıcı olmadan, doğrudan standart komutla çalıştır:

   ```powershell
   .venv\Scripts\python.exe -m pytest tests/ -q
   ```

   Beklenen: 559 başarılı. `.env` dosyan yerinde dururken de geçmeli.

2. Çalıştırmadan sonra `data/` dizinine bak. `debug.log` boyutu artmamalı, yeni `google_token_*.json` dosyası oluşmamalı:

   ```powershell
   Get-ChildItem data | Select-Object Name, Length, LastWriteTime
   ```

3. Uygulamanın normal çalışmaya devam ettiğini doğrula; izolasyon yalnızca test sürecine ait olmalı, ürün davranışı değişmemeli:

   ```powershell
   .venv\Scripts\python.exe -m src.ui.app
   ```

   Giriş yap, bir öneriyi aç, asistana kısa bir mesaj yaz. `.env`’deki Gemini ayarı normal şekilde okunmalı ve `data\debug.log` yeniden yazılmalı.

**Bu kontrol noktasının sınırı ve sıradaki iş**

`data/` dizinindeki eski test artığı dosyalar (`google_token_acc1.json`, `google_token_newuser.json`) bu değişiklikle silinmedi; yalnızca yenilerinin oluşması engellendi. Bunları silmek istersen ayrıca söyle, kendiliğinden kaldırmadım.

Ağ engeli Python soket katmanında çalışıyor; kendi soketini açan native bir kütüphane bu engele takılmaz. Bir test içinde geç çağrılan `import` ile alınan sabit kopyası da yamalanamaz; bu nedenle modül seviyesinde içe aktarma varsayımı korunmalı.

Yol haritasındaki ilk sprint listesinde sırada **4. madde** var: varsayılan içerik loglarını kapatma/maskeleme (PRIV-01’in log yarısı). Şu an ham model girdisi/çıktısı ve e-posta içeriği `data/debug.log` dosyasına yazılabiliyor.

**Kontrol noktası 5 — PRIV-01 (log yarısı): varsayılan log içeriği maskeleme + rotasyon**

Durum: kullanıcı tarafından onaylandı. Kod ve testler `b0e66fc` (`Mask sensitive log content by default, add log rotation`) commit'iyle kaydedildi.

PRIV-01 bulgusunun iki parçası vardı: token’ların şifreli saklanması (sunucuya taşınırsa gereken ayrı bir anahtar-yönetimi işi, bu kontrol noktasının kapsamı DIŞINDA — yalnızca log yarısı ele alındı) ve "varsayılan loglarda içerik maskeleme, rotasyon, saklama süresi". Önceden `data/debug.log`’a her LLM çağrısının sistem/kullanıcı promptu, ham model çıktısı ve mail konusu (subject) tam metin olarak yazılıyordu — dosya şifrelenmeden diske yazıldığı için bu, mail gövdesi/sohbet metni gibi hassas içeriğin okunabilir biçimde diskte birikmesi anlamına geliyordu. Gerçek kurulumda tek dosya 8MB’ı geçmişti; rotasyon da yoktu.

Yeni davranış:

- `src/core/logging_config.py::redact()` — bir metni varsayılan olarak ilk 80 karakter + kalan uzunluk + kararlı bir kısa SHA-256 özetine indirger (`text[:80]…[N more chars redacted, sha256=xxxxxxxx]`). 80 karakterin altındaki kısa metinler (çoğu mail konusu gibi) OLDUĞU GİBİ kalır — maskeleme yalnızca uzun/serbest metinlerde devreye giriyor. Hash sayesinde "aynı içerik tekrar mı geldi" gibi karşılaştırmalar hâlâ mümkün, gerçek metin dosyada görünmüyor.
- Bu, her LLM çağrısının ortak boğazı olan `json_generation.py::generate_json`/`generate_json_from_file`’a uygulandı: kullanıcı promptu, ham model çıktısı ve ayrıştırılmış JSON artık maskeli yazılıyor. Kendi yazdığımız sistem promptu (kullanıcı içeriği değil) maskelenmedi — tam görünmesi hata ayıklamada değerli, gizlilik riski yok.
- `mail_analysis.py`’deki beş, `scan_inbox.py`’deki üç log satırındaki mail konusu (subject) de aynı fonksiyonla maskeleniyor.
- `LOG_CONTENT=full` ortam değişkeni maskelemeyi devre dışı bırakıyor — yerel, aktif hata ayıklama için bilinçli bir kaçış kapısı (bkz. `.env.example`), sunucu/paylaşılan bir kurulumda set edilmemesi gerektiği not edildi.
- Log dosyası artık `RotatingFileHandler` ile yazılıyor: ~10MB’a ulaşınca döner, en fazla 5 eski dosya tutulur (~60MB tavan). Zaman bazlı bir saklama süresi (örn. "30 günden eskisini sil") ayrı bir scheduler gerektirirdi — tek-kullanıcılı yerel uygulama için boyut bazlı rotasyonun yeterli bir sınır olduğuna karar verildi, bu bilinçli bir kapsam kararı.

Kapsam dışı bırakılanlar: OAuth token dosyalarının şifreli saklanması (PRIV-01’in diğer yarısı, ayrı bir madde olarak kalmalı — sunucu dağıtımı seçilirse OS güvenli deposu/anahtar yönetimi gerektiriyor); istisna traceback’lerinin maskelenmesi (`logger.exception(...)` çağrıları — Python’un standart traceback biçimi zaten yerel değişken değerlerini basmıyor, yalnızca çağrı yığını + istisna mesajı; bir istisna mesajının kendi içine ham kullanıcı içeriği gömdüğü nadir bir durum bu kapsamda ayrıca aranmadı).

Değişen dosyalar:

- [logging_config.py](../src/core/logging_config.py): `redact()`, `RotatingFileHandler`.
- [json_generation.py](../src/providers/json_generation.py): tüm LLM çağrılarının ortak boğazında maskeleme.
- [mail_analysis.py](../src/services/mail_analysis.py): beş log satırında mail konusu maskeleme.
- [scan_inbox.py](../src/services/scan_inbox.py): üç log satırında mail konusu maskeleme.
- [.env.example](../.env.example): `LOG_CONTENT=full` kaçış kapısının belgelenmesi.
- [test_logging_config.py](../tests/test_logging_config.py): 12 yeni test — `redact()`’in kendisi (kısa/uzun metin, determinizm, farklı içerik farklı hash, `LOG_CONTENT=full`), rotasyon yapılandırması, ve gerçek `generate_json()` çağrısının log dosyasına yazdığı satırı okuyarak hem kullanıcı promptunun hem ham model çıktısının gerçekten maskelendiğinin, sistem promptunun maskelenmediğinin uçtan uca doğrulanması.

Doğrulama: maskeleme testinin gerçekten bir şey yakaladığını varsaymadım — `redact(user_prompt)` çağrısını geçici olarak `user_prompt`’a döndürüp testin kırıldığını ve tam gizli içeriği log dosyasında gösterdiğini doğruladım, sonra değişikliği geri aldım. Tam paket (TEST-01 izolasyonuyla, sarmalayıcı olmadan) **571 başarılı**, 13 deprecation uyarısı. Gerçek `data/` dizini bu çalıştırmadan önce/sonra dosya bazında (md5) karşılaştırıldı, değişmedi. pyflakes değişen dosyalarda temiz.

**Senin yapacağın manuel kontrol**

1. Uygulamayı durdurup yeniden başlat, sonra normal bir işlem yap (bir mail taraması tetikle ya da asistana bir mesaj yaz):

   ```powershell
   .venv\Scripts\python.exe -m src.ui.app
   ```

2. `data\debug.log`’un SON kısmına bak (`Get-Content data\debug.log -Tail 50`). `generate_json call | system=... | user=...` gibi satırlarda kullanıcı tarafının (`user=`) artık `[N more chars redacted, sha256=...]` ile kısaltıldığını, tam mail/sohbet metninin görünmediğini doğrula. Sistem promptunun (kendi şablonumuz) hâlâ tam göründüğünü de görebilirsin — bu kasıtlı.
3. İstersen `LOG_CONTENT=full` satırını `.env`’e ekleyip uygulamayı yeniden başlat, aynı işlemi tekrarla — bu sefer tam metnin göründüğünü doğrula, sonra satırı `.env`’den kaldır (varsayılan davranışa dönmek için).
4. `data\debug.log`’un artık ~10MB’ı geçince `debug.log.1` gibi dönen dosyalar üretip üretmediğini bugün gözlemlemen gerekmiyor (dosya zaten büyükse bir sonraki yeniden başlatmada otomatik döner) — istersen mevcut büyük log dosyasını silip yeniden oluşmasını izleyebilirsin, zorunlu değil.

**Bu kontrol noktasının sınırı ve sıradaki iş**

`data/debug.log` dosyasının bugüne kadar birikmiş, maskesiz geçmiş içeriği bu değişiklikle temizlenmedi — yalnızca BUNDAN SONRA yazılacak satırlar maskeleniyor. İstersen eski dosyayı silmemi söyle, kendiliğinden silmedim. OAuth token’larının şifreli saklanması (PRIV-01’in diğer yarısı) hâlâ açık; bu, sunucu dağıtımı kararının netleşmesini bekleyen ayrı bir iş olarak bırakıldı (bkz. yol haritası "İlk mimari kararda iki dağıtım yolu netleştirilmeli").

**Kontrol noktası 6 — EVENT-01: önizleme → connector hatırlatıcı sözleşmesi**

Durum: kullanıcı tarafından canlı test edilip onaylandı (gerçek Google Calendar'da hatırlatıcının eklendiği doğrulandı). Kod ve testler `d5ff5de` (`Pass candidate reminders through to the calendar connector (EVENT-01)`) commit'iyle kaydedildi.

EVENT-01 bulgusu: `CandidateEvent.reminders` (bkz. `src/core/models.py::ReminderSpec`) model/DB/önizlemede zaten vardı — bir kural ("3 gün önce hatırlat" gibi, `reminder_minutes_before` structured_action) `apply_retrieved_policies` ile bir candidate'a otomatik uygulanabiliyor, CLI'nın önizleme ekranında gösteriliyor, `candidate_events` tablosunda saklanıyordu. Ama gerçek `create_event`/`update_event` çağrılarının HİÇBİRİ (`vertical_prototype.py`, `chat_flow.py`, `routes.py` — 4 çağrı noktası) bu alanı connector'a geçirmiyordu — kullanıcı "3 gün önce hatırlat" kuralını onaylasa bile Google/Outlook takviminde hiçbir hatırlatıcı oluşmuyordu, sessizce kayboluyordu. Recurrence (tekrar) ve katılımcı alanları bu candidate akışında hiç doldurulmadığı için (yol haritasının aynı maddesinde anılsa da) gerçek bir veri kaybı yok — bu kontrol noktası kapsamı bilinçli olarak yalnızca hatırlatıcıya odaklandı.

Yeni davranış:

- `src/connectors/base.py::CalendarConnector.create_event`/`update_event` artık `reminders: list[dict] | None = None` alıyor — sağlayıcıdan bağımsız şekil: `[{"minutes_before": int}, ...]`. `None` = hiç belirtilmedi (dokunma), `[]` = hatırlatıcıları açıkça kaldır (yalnızca `update_event`'te anlamlı).
- `GoogleCalendarConnector`: `reminders` verilirse Google'ın `{"useDefault": False, "overrides": [{"method": "popup", "minutes": N}, ...]}` şekline çeviriyor — Google BİRDEN FAZLA hatırlatıcıyı destekliyor, hepsi override olarak gidiyor (API'nin 5 override sınırına göre kırpılıyor, aşılırsa loglanıyor).
- `MSCalendarConnector`: Graph, Google'ın aksine etkinlik başına yalnızca TEK bir hatırlatıcı alanı sunuyor (`isReminderOn` + `reminderMinutesBeforeStart`). Birden fazla `reminders` verilirse etkinliğe EN YAKIN olanı (`minutes_before` en küçük) tutuluyor, geri kalanı SESSİZCE atılmıyor — bir `logger.warning` ile kaç tanesinin ve hangisinin düştüğü kaydediliyor (yol haritasının "Google ve Microsoft yetenek farkları görünür" kabul kriterinin karşılığı).
- 4 çağrı noktası (`vertical_prototype.py` CLI onayı, `chat_flow.py` web sohbet onayı, `routes.py` normal onay + mail-kaynaklı UPDATE_SUGGESTED onayı) artık `reminders=[r.model_dump() for r in candidate.reminders] or None` geçiriyor — candidate'ta hiç hatırlatıcı yoksa (çoğu durum) `None` gidip sağlayıcı davranışını hiç değiştirmiyor, eski davranışla birebir aynı.
- Bugün pratikte `candidate.reminders` en fazla TEK öge taşıyor (`apply_retrieved_policies`'in `break` ile ilk eşleşen kuralda durması) — çoklu-hatırlatıcı/kapasite-farkı kodu şu an aktif kullanılmayan ama test edilmiş bir gelecek-hazırlığı.

Kapsam dışı bırakılanlar: tekrar (`recurrence`) ve katılımcı (`participants`) alanlarının connector'a aktarılması — bu candidate akışında hiçbir yerde doldurulmadıkları için şu an gerçek bir veri kaybı yok, ayrı bir iş olarak bırakıldı. `vertical_prototype.py`/`chat_flow.py`'nin KONUŞMA akışından gelen `update_event` intent'i (mevcut bir Google/MS etkinliğini taşıma, `handle_update_event`) hiç dokunulmadı — o akış ham takvim event'i üzerinde çalışıyor, `CandidateEvent`/reminders kavramı yok, bu değişiklikle ilgisiz.

Değişen dosyalar:

- [base.py](../src/connectors/base.py), [google_calendar.py](../src/connectors/google_calendar.py), [ms_calendar.py](../src/connectors/ms_calendar.py): `reminders` parametresi + sağlayıcıya özgü çeviri.
- [vertical_prototype.py](../src/services/vertical_prototype.py), [chat_flow.py](../src/services/chat_flow.py), [routes.py](../src/ui/routes.py): 4 çağrı noktasında `candidate.reminders` artık geçiriliyor.
- [test_calendar_connectors.py](../tests/test_calendar_connectors.py) (yeni, 9 test): Google/Microsoft connector'larının gerçek istek gövdesini (Google için sahte `_service` chain, Microsoft için sahte `requests.request` — gerçek API'ye hiç dokunulmuyor) doğruluyor — bu, projede Google Calendar/MS Calendar connector'ları için İLK connector-seviyesi test dosyası (CLAUDE.md'nin daha önce işaret ettiği eksik).
- [test_ui_routes.py](../tests/test_ui_routes.py): `_pending_candidate_for_account` artık `reminders` parametresi alıyor; yeni `test_approve_passes_candidate_reminders_to_create_event` web onay akışının uçtan uca doğru geçirdiğini kanıtlıyor.
- [test_chat_flow.py](../tests/test_chat_flow.py), [test_chat_state.py](../tests/test_chat_state.py): sahte `FakeCalendar`/`_NullCalendar` sınıfları yeni `reminders` parametresini kabul edecek şekilde güncellendi (aksi halde `TypeError` ile tüm sohbet testleri kırılıyordu).

Doğrulama: hem Google hem Microsoft tarafında testlerin gerçekten bir şey yakaladığını varsaymadım — Google'da `reminders` atamasını geçici olarak kaldırıp ilgili 2 testin `KeyError: 'reminders'` ile kırıldığını, Microsoft'ta "en yakını tut" mantığını `min`'den `max`'e çevirip ilgili testin yanlış değeri (`4320` yerine beklenen `30`) yakaladığını doğruladım, sonra ikisini de geri aldım. Tam paket **581 başarılı** (571 eski + 10 yeni: 9 connector + 1 route testi), pyflakes değişen tüm dosyalarda temiz.

**Senin yapacağın manuel kontrol**

1. Kurallarım'da "hatırlatıcı" tipinde yeni bir kural tanımla (örn. event_type=Sınav, "3 gün önce" / 4320 dakika) — ya da mevcut bir kuralın olduğu bir mail/sohbet senaryosunu kullan.
2. O kurala uyan bir etkinliği (mail taraması veya sohbet üzerinden) oluşturup onayla.
3. Google Calendar'ı (gerçek hesap) açıp o etkinliğin detayına bak — artık bir hatırlatıcının (bildirim) gerçekten eklendiğini doğrula. Önceden bu adımda HİÇBİR hatırlatıcı görünmüyordu.
4. Bir Outlook hesabın varsa aynı senaryoyu orada da dene — Outlook etkinliğinde de tek bir hatırlatıcının göründüğünü doğrula.

**Bu kontrol noktasının sınırı ve sıradaki iş**

Tekrar (recurrence) ve katılımcı (participants) alanlarının connector sözleşmesine eklenmesi bilinçli olarak kapsam dışı bırakıldı — bugün hiçbir candidate akışı bu alanları doldurmuyor, dolayısıyla gerçek bir veri kaybı yok; ileride bu alanlar doldurulmaya başlarsa aynı desenle (base.py imza + Google/MS çeviri + çağrı noktaları) eklenebilir.

**Kullanıcının canlı testinde bulunan ek hata (2026-09-11, aynı kontrol noktası kapsamında düzeltildi):** hatırlatıcı gerçekten eklendi (doğrulandı), ama web sohbetinde arayüz dili İngilizce ayarlıyken "(Kural uygulandı: ...)" mesajı hep TÜRKÇE basıldı. Kök neden: `vertical_prototype.py::apply_retrieved_policies` bu mesajı sabit bir Türkçe f-string ile kuruyordu ve `chat_flow.py`'nin 3 çağrı noktası hiç `lang` geçmiyordu (fonksiyonun kendisinde `lang` parametresi bile yoktu). Düzeltme: yeni `chat.rule_applied` çeviri anahtarı (`src/localization/catalog.py`) + `apply_retrieved_policies`'e `lang: str = "tr"` parametresi — CLI'ya basılan `print()` çıktısı BİLEREK hâlâ sabit Türkçe (CLI zaten hiç lokalize değil), yalnızca web'e dönen `applied_messages` listesi artık `lang`'a göre çevriliyor. `chat_flow.py`'nin 3 çağrı noktası da `lang=lang` geçecek şekilde güncellendi. Yeni test: `tests/test_chat_flow.py::test_applied_policy_message_is_localized` — `lang="en"`/`"tr"` ile gerçek bir kural uygulayıp dönen mesajın doğru dilde olduğunu doğruluyor; `translate(...)` çağrısını geçici olarak eski sabit mesaja döndürüp testin kırıldığını görerek doğrulandı, sonra geri alındı. Tam paket **582 başarılı**, pyflakes temiz.

**Kontrol noktası 7 — INPUT-01: dosya/ses yükleme sınırları + gerçek tür doğrulama**

Durum: kullanıcı tarafından onaylandı. Kod ve testler `bcb5342` (`Bound and validate chat/mail file uploads (INPUT-01)`) commit'iyle kaydedildi.

INPUT-01 bulgusu: dosya (`dosya`) ve ses (`ses`) yükleme rotaları `.file.read()` ile TÜM gövdeyi sınırsız okuyup ANCAK SONRA (dosyada) boyut kontrolü yapıyordu — kötü niyetli/kazara çok büyük bir yükleme, reddedilmeden önce tamamen belleğe/diske alınmış oluyordu; ses tarafında (mikrofon) HİÇBİR boyut sınırı yoktu (dosyanınki gibi post-hoc bir kontrol bile). Ayrıca hem sohbet yüklemesi hem mail eki yolu, dosya türünü yalnızca istemcinin/mailin BEYAN ettiği Content-Type'a (`dosya.content_type`/`attachment.content_type`) bakarak kabul ediyordu — bu alan tamamen istemci/gönderen kontrolünde, gerçek baytlarla hiç doğrulanmıyordu.

Yeni davranış:

- `src/services/vertical_prototype.py` — `MAX_FILE_SIZE_BYTES` (chat_flow.py'den taşındı) + yeni `MAX_AUDIO_SIZE_BYTES` (ikisi de 10 MB), `ACCEPTED_FILE_MIME_TYPES` ile AYNI "tek kaynak" desenini izliyor. Yeni `sniff_file_mime_type(data)`: ilk baytlara (sihirli sayı — PDF `%PDF-`, PNG/JPEG/WEBP standart imzaları, HEIC/HEIF ISO-BMFF `ftyp` kutusu) bakarak GERÇEK türü tespit ediyor. Yeni `file_content_matches_declared_type(declared, data)`: sniff sonucunu beyan edilen türle karşılaştırıyor (HEIC/HEIF aynı aile sayılıyor — brand koduyla güvenilir ayrım yapılamıyor).
- `src/ui/chat_routes.py`: hem `dosya.file.read(...)` hem `ses.file.read(...)` artık sınırsız değil, `cap + 1` bayt okuyor — gövde ne kadar büyük olursa olsun bellekte/diskte tutulan miktar sınırlı kalıyor, kontrolün kendisi tam boyutu bilmeye gerek kalmadan yapılabiliyor. Ses için yeni bir boyut kontrolü eklendi (öncesinde HİÇ yoktu) — aşılırsa transkripsiyon hiç denenmeden yeni `chat.audio.too_large` mesajıyla erken dönülüyor. Tekrarlanan "hata balonu ekle + yanıt oluştur" deseni `_error_bubble_response` yardımcı fonksiyonuna çıkarıldı (transkripsiyon hatası + boyut reddi aynı şekli paylaşıyor).
- `src/services/chat_flow.py::_dispatch_file_upload`: mevcut boyut kontrolünden SONRA yeni bir `file_content_matches_declared_type` kontrolü eklendi — beyan edilen tür kabul listesindeyse ve boyut sınırın altındaysa bile gerçek baytlar uyuşmuyorsa (`chat.file.unsupported_type` mesajıyla) reddediliyor, LLM'e hiç gönderilmiyor.
- `src/services/mail_analysis.py::extract_candidate_from_email_with_attachments`: iki yeni kontrol. (1) `attachment.size_bytes` (Gmail/Graph zaten mail listesi metadata'sında bunu döndürüyor) `MAX_FILE_SIZE_BYTES`'ı aşıyorsa, gerçek baytlar HİÇ İNDİRİLMİYOR. (2) İndirildikten sonra `file_content_matches_declared_type` ile mailin beyan ettiği `content_type` gerçek baytlarla çapraz kontrol ediliyor — mail göndereni bu alanı istediği gibi ayarlayabildiği için (attacker-controlled), sniff olmadan güvenilir değildi.
- Yeni çeviri anahtarı: `chat.audio.too_large` (TR/EN).

Değişen dosyalar:

- [vertical_prototype.py](../src/services/vertical_prototype.py): `MAX_FILE_SIZE_BYTES`/`MAX_AUDIO_SIZE_BYTES`/`sniff_file_mime_type`/`file_content_matches_declared_type`.
- [chat_routes.py](../src/ui/chat_routes.py): sınırlı okuma, ses boyutu kontrolü, `_error_bubble_response`.
- [chat_flow.py](../src/services/chat_flow.py): içerik-tür doğrulama, sabit taşındı.
- [mail_analysis.py](../src/services/mail_analysis.py): ek boyutu ön-kontrolü (indirmeden önce) + indirilen baytların tür doğrulaması.
- [catalog.py](../src/localization/catalog.py): `chat.audio.too_large`.
- [test_file_type_sniffing.py](../tests/test_file_type_sniffing.py) (yeni, 9 test): sniff/eşleşme fonksiyonlarının kendisi + `_dispatch_file_upload`'ın sahte içerik/aşırı boyut karşısında LLM'e HİÇ gitmediğini (bilerek patlayan bir `_RaisingVisionLLM` ile) kanıtlıyor.
- [test_mail_attachments.py](../tests/test_mail_attachments.py): 2 yeni test — oversized ek indirilmeden reddediliyor, sahte content_type'lı ek reddediliyor.
- [test_chat_routes.py](../tests/test_chat_routes.py): 1 yeni test (aşırı büyük ses transkribe edilmeden reddediliyor) + var olan dosya-yükleme testlerinin sahte baytları gerçek JPEG imzasıyla (`\xff\xd8\xff`) güncellendi (aksi halde yeni sniff kontrolü onları da reddederdi).
- [test_scan_inbox.py](../tests/test_scan_inbox.py): aynı sebeple sahte ek baytı gerçek JPEG imzasına güncellendi.

Doğrulama: üç ayrı noktada testlerin gerçekten bir şey yakaladığını varsaymadım — `file_content_matches_declared_type`'ı geçici olarak hep `True` döndürecek şekilde bozup 3 testin (`test_content_mismatch_is_rejected`, `test_dispatch_file_upload_rejects_spoofed_content_type`, `test_attachment_with_spoofed_content_type_is_rejected`) kırıldığını; `chat_routes.py`'deki ses boyutu kontrolünü geçici devre dışı bırakıp reddedilmesi gereken bir transkriptin gerçekten niyet sınıflandırmaya kadar sızdığını (log'da görüldü) ve testin bunu yakaladığını; `mail_analysis.py`'deki boyut ön-kontrolünü geçici devre dışı bırakıp oversized bir ekin indirilip işlendiğini ve testin bunu yakaladığını doğruladım — üçünde de sonra geri aldım. Tam paket **592 başarılı** (582 eski + 10 yeni), pyflakes değişen tüm dosyalarda temiz.

**Senin yapacağın manuel kontrol**

1. Sohbette normal boyutlu bir fotoğraf/PDF yükleyip her zamanki gibi çalıştığını doğrula (regresyon kontrolü).
2. İstersen 10 MB'ı geçen bir dosya yükleyip "çok büyük" mesajını gör.
3. Normal bir sesli mesaj kaydedip her zamanki gibi çalıştığını doğrula (regresyon kontrolü) — asıl riskli değişiklik ses tarafında, hiç test edilmemişti.

**Bu kontrol noktasının sınırı ve sıradaki iş**

Kapsam dışı bırakılanlar (yol haritasının aynı maddesinde anılan ama ayrı işler): **kullanıcı kotası** (günlük/haftalık yükleme sayısı sınırı — kalıcı sayaç + sıfırlama penceresi gerektiren ayrı bir özellik); **genel istek gövdesi sınırı** (ASGI/middleware seviyesinde tüm rotalar için `Content-Length` kontrolü — bu checkpoint yalnızca chat/mail dosya yollarını, gerçek risk yüzeyini kapsıyor, ASGI seviyesi OPS-01'e daha yakın); **ses SÜRESİ sınırı** (saniye cinsinden) — bir byte-boyutu sınırı (10 MB) dolaylı olarak bunu da sınırlıyor (webm/opus sıkıştırmasıyla saatler süren bir kayıt gerektirir), gerçek bir süre sınırı ses konteynerini decode etmeyi gerektirirdi, tek-kullanıcılı yerel bir uygulama için orantısız bulundu.
