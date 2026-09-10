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

Durum: uygulandı, otomatik doğrulama tamamlandı; kullanıcı testi ve onayı bekleniyor. Bu adım henüz commit edilmedi.

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
