"""UI metin kataloğu (bkz. docs/architecture-plan.md §15, CLAUDE.md Web UI notları).

Anahtar-önce sözlük (dil-önce değil): eksik bir çeviri o zaman diff'te
görünür olur, ve tests/test_localization.py::test_no_missing_translations
bunu mekanik olarak doğrulayabilir. JSON/gettext DEĞİL — derleme adımı yok
(offline-first), doğrudan import edilebilir, pyflakes syntax hatalarını
yakalar.

İsimlendirme: `alan.oge` (nav.takvim, common.cancel). Enum etiketleri kendi
ad alanında: `enum.<enum_adı>.<değer>` — böylece her rozet/durum gösterimi
render'da otomatik olarak çevrilmiş bir KELİME basar, salt renk değil
(bkz. §16 "renk tek başına anlam taşımaz")."""

from __future__ import annotations

SUPPORTED_LANGUAGES: tuple[str, ...] = ("tr", "en")
DEFAULT_LANGUAGE = "tr"

MESSAGES: dict[str, dict[str, str]] = {
    # --- Genel ---
    "app.brand": {"tr": "Calendar Agent", "en": "Calendar Agent"},
    "common.approve": {"tr": "Onayla", "en": "Approve"},
    "common.edit": {"tr": "Düzenle", "en": "Edit"},
    "common.reject": {"tr": "Reddet", "en": "Reject"},
    "common.save": {"tr": "Kaydet", "en": "Save"},
    "common.cancel": {"tr": "Vazgeç", "en": "Cancel"},
    "common.unspecified": {"tr": "(belirtilmedi)", "en": "(not specified)"},
    "common.untitled": {"tr": "(başlıksız)", "en": "(untitled)"},
    "common.minutes": {"tr": "dakika", "en": "minutes"},
    "common.active": {"tr": "Aktif", "en": "Active"},
    "common.inactive": {"tr": "Pasif", "en": "Inactive"},
    "common.skip_to_content": {"tr": "İçeriğe geç", "en": "Skip to content"},
    "common.language": {"tr": "Dil", "en": "Language"},
    "common.accounts": {"tr": "Hesaplar", "en": "Accounts"},
    "common.primary_navigation": {"tr": "Ana navigasyon", "en": "Primary navigation"},
    "common.more": {"tr": "Diğer", "en": "More"},

    # --- Sol navigasyon (§16) ---
    "nav.anasayfa": {"tr": "Ana Sayfa", "en": "Home"},
    "nav.takvim": {"tr": "Takvim", "en": "Calendar"},
    "nav.oneriler": {"tr": "Gelen Öneriler", "en": "Suggestions"},
    "nav.hesaplar": {"tr": "E-posta Hesapları", "en": "Email Accounts"},
    "nav.kurallarim": {"tr": "Kurallarım", "en": "My Rules"},
    "nav.duzeltmelerim": {"tr": "Düzeltmelerim", "en": "My Corrections"},
    "nav.ayarlar": {"tr": "Ayarlar", "en": "Settings"},

    # --- 404 ---
    "notfound.title": {"tr": "Sayfa bulunamadı", "en": "Page not found"},
    "notfound.body": {
        "tr": "Aradığınız sayfa mevcut değil.",
        "en": "The page you're looking for doesn't exist.",
    },
    "notfound.back_home": {"tr": "Ana Sayfa'ya dön", "en": "Back to Home"},

    # --- Gelen Öneriler ---
    "oneriler.title": {"tr": "Gelen Öneriler", "en": "Suggestions"},
    "oneriler.empty": {"tr": "Şu an bekleyen öneri yok.", "en": "No pending suggestions right now."},
    "oneriler.field.time": {"tr": "Zaman", "en": "Time"},
    "oneriler.field.location": {"tr": "Konum", "en": "Location"},
    "oneriler.field.type": {"tr": "Tür", "en": "Type"},
    "oneriler.field.importance": {"tr": "Önem", "en": "Importance"},
    "oneriler.field.source": {"tr": "Kaynak", "en": "Source"},
    "oneriler.warning.incomplete": {
        "tr": "Eksik/belirsiz bilgi var — onaylamadan önce düzenleyin.",
        "en": "Missing or ambiguous information — edit before approving.",
    },
    "oneriler.reject_reason_placeholder": {
        "tr": "Reddetme sebebi (isteğe bağlı)",
        "en": "Reason for rejecting (optional)",
    },

    # --- Düzenle ---
    "duzenle.title": {"tr": "Düzenle", "en": "Edit"},
    "duzenle.source": {"tr": "Kaynak", "en": "Source"},
    "duzenle.warning.incomplete": {"tr": "Eksik/belirsiz alanlar", "en": "Missing/ambiguous fields"},
    "duzenle.field.title": {"tr": "Başlık", "en": "Title"},
    "duzenle.field.datetime": {"tr": "Tarih/saat (YYYY-MM-DDTHH:MM:SS)", "en": "Date/time (YYYY-MM-DDTHH:MM:SS)"},
    "duzenle.field.duration": {"tr": "Süre (dakika)", "en": "Duration (minutes)"},
    "duzenle.field.importance": {"tr": "Önem", "en": "Importance"},
    "duzenle.field.location": {"tr": "Konum", "en": "Location"},

    # --- Çakışma onayı ---
    "cakisma.title": {"tr": "Çakışma bulundu", "en": "Conflict found"},
    "cakisma.conflicts_with": {
        "tr": "şu etkinlik(ler)le çakışıyor:",
        "en": "conflicts with the following event(s):",
    },
    "cakisma.approve_anyway": {"tr": "Yine de ekle", "en": "Add anyway"},

    # --- E-posta Hesapları ---
    "hesaplar.title": {"tr": "E-posta Hesapları", "en": "Email Accounts"},
    "hesaplar.empty": {
        "tr": "Henüz bağlı bir hesap yok. Aşağıdan bir hesap ekleyin.",
        "en": "No connected accounts yet. Add one below.",
    },
    "hesaplar.add_account": {"tr": "Yeni hesap ekle", "en": "Add new account"},
    "hesaplar.account_added": {"tr": "Hesap başarıyla eklendi.", "en": "Account added successfully."},
    "hesaplar.oauth_error.reddedildi": {
        "tr": "Google onayı iptal edildi, hesap eklenmedi.",
        "en": "Google consent was cancelled — no account was added.",
    },
    "hesaplar.oauth_error.gecersiz": {
        "tr": "Bir şeyler ters gitti (geçersiz oturum), tekrar dener misiniz?",
        "en": "Something went wrong (invalid session) — could you try again?",
    },
    "hesaplar.oauth_error.basarisiz": {
        "tr": "Hesap eklenemedi, tekrar dener misiniz?",
        "en": "Couldn't add the account — could you try again?",
    },
    "hesaplar.oauth_error.client_yok": {
        "tr": "OAuth yapılandırması eksik (google_oauth_client.json bulunamadı).",
        "en": "OAuth configuration is missing (google_oauth_client.json not found).",
    },
    "hesaplar.field.provider": {"tr": "Sağlayıcı", "en": "Provider"},
    "hesaplar.field.status": {"tr": "Durum", "en": "Status"},
    "hesaplar.field.connected_at": {"tr": "Bağlanma tarihi", "en": "Connected on"},
    "hesaplar.scan_now": {"tr": "Şimdi tara", "en": "Scan now"},
    "hesaplar.scan_hint": {
        "tr": "Tarama birkaç dakika sürebilir, sayfa o süre boyunca bekler.",
        "en": "Scanning may take a few minutes; the page will wait.",
    },
    "hesaplar.scan_summary": {
        "tr": "Tarama tamamlandı: {total} mail kontrol edildi, {found} öneri kuyruğa eklendi.",
        "en": "Scan complete: {total} emails checked, {found} suggestions queued.",
    },
    "hesaplar.scan_busy": {
        "tr": "Bu hesap için bir tarama zaten sürüyor, bitmesini bekleyin.",
        "en": "A scan for this account is already running — please wait for it to finish.",
    },
    "hesaplar.scan_errors": {
        "tr": "({errors} mail işlenemedi, sonraki taramada tekrar denenecek.)",
        "en": "({errors} emails could not be processed and will be retried on the next scan.)",
    },

    # --- Ana Sayfa ---
    "anasayfa.greeting": {"tr": "Merhaba", "en": "Hello"},
    "anasayfa.no_account_hint": {
        "tr": "Başlamak için bir e-posta hesabı bağlayın.",
        "en": "Connect an email account to get started.",
    },
    "anasayfa.today_events": {"tr": "Bugün", "en": "Today"},
    "anasayfa.no_events_today": {"tr": "Bugün için etkinlik yok.", "en": "No events today."},
    "anasayfa.calendar_unavailable": {
        "tr": "Bugünkü etkinlikler şu an gösterilemiyor.",
        "en": "Today's events can't be shown right now.",
    },
    "anasayfa.pending_heading": {"tr": "Bekleyen öneriler", "en": "Pending suggestions"},
    "anasayfa.pending_none": {"tr": "Bekleyen öneri yok.", "en": "No pending suggestions."},
    "anasayfa.view_all": {"tr": "Tümünü gör", "en": "View all"},
    "anasayfa.others_pending": {
        "tr": "diğer hesap(lar)da {n} öneri daha bekliyor",
        "en": "{n} more suggestion(s) waiting on other account(s)",
    },
    "anasayfa.stat_pending": {"tr": "Bekleyen öneri", "en": "Pending suggestions"},
    "anasayfa.stat_rules": {"tr": "Aktif kural", "en": "Active rules"},
    "anasayfa.scan_cta": {"tr": "Gelen kutusunu şimdi tara", "en": "Scan inbox now"},

    # --- Asistan slotu (bkz. plan: chatbox ayrı dilim, slot şimdi ayrılıyor) ---
    "assistant.title": {"tr": "Asistana yaz", "en": "Ask the assistant"},
    "assistant.coming_soon": {"tr": "Yakında", "en": "Coming soon"},
    "assistant.description": {
        "tr": "Konuşarak etkinlik oluşturma ve takvim sorgulama yakında burada — model tamamen yerelde çalışıyor.",
        "en": "Creating events and querying your calendar by chatting is coming here soon — the model runs fully on your device.",
    },
    "assistant.example1": {"tr": "\"Yarın 14:00'te Ahmet'le toplantı ekle\"", "en": "\"Add a meeting with Alex tomorrow at 2pm\""},
    "assistant.example2": {"tr": "\"Bu hafta takvimimde ne var?\"", "en": "\"What's on my calendar this week?\""},
    "assistant.placeholder": {
        "tr": "Asistana yazın (yakında)...",
        "en": "Message the assistant (coming soon)...",
    },

    # --- Takvim ---
    "takvim.no_account": {
        "tr": "Takvimi görmek için önce bir e-posta hesabı bağlayın.",
        "en": "Connect an email account first to see your calendar.",
    },
    "takvim.no_token": {
        "tr": "Bu hesabın takvim bağlantısı yenilenmeli.",
        "en": "This account's calendar connection needs to be renewed.",
    },
    "takvim.no_token_hint": {
        "tr": "CLI üzerinden (`python -m src.services.vertical_prototype`) bu hesapla tekrar giriş yapın.",
        "en": "Sign in again with this account from the CLI (`python -m src.services.vertical_prototype`).",
    },
    "takvim.error": {
        "tr": "Takvim şu anda yüklenemedi.",
        "en": "The calendar couldn't be loaded right now.",
    },
    "takvim.error_details": {"tr": "Teknik ayrıntı", "en": "Technical details"},
    "takvim.prev_week": {"tr": "Önceki hafta", "en": "Previous week"},
    "takvim.today": {"tr": "Bugün", "en": "Today"},
    "takvim.next_week": {"tr": "Sonraki hafta", "en": "Next week"},

    # --- Kurallarım ---
    "kurallarim.title": {"tr": "Kurallarım", "en": "My Rules"},
    "kurallarim.empty": {
        "tr": "Henüz bir kural tanımlamadınız.",
        "en": "You haven't defined any rules yet.",
    },
    "kurallarim.new_rule": {"tr": "Yeni kural", "en": "New rule"},
    "kurallarim.inactive_heading": {"tr": "Pasif kurallar ({n})", "en": "Inactive rules ({n})"},
    "kurallarim.deactivate": {"tr": "Pasifleştir", "en": "Deactivate"},
    "kurallarim.reactivate": {"tr": "Aktifleştir", "en": "Reactivate"},
    "kurallarim.reactivate_conflict": {
        "tr": "Bu kural aktifleştirilemedi — aynı kapsamda başka bir aktif kural zaten var.",
        "en": "This rule couldn't be reactivated — another active rule already covers the same scope.",
    },
    "kurallarim.field.version": {"tr": "Sürüm", "en": "Version"},
    "kurallarim.scope.global": {"tr": "Tüm etkinlikler", "en": "All events"},
    "kurallarim.scope.event_type": {"tr": "Tür: {value}", "en": "Type: {value}"},
    "kurallarim.scope.sender": {"tr": "Gönderen: {value}", "en": "Sender: {value}"},
    "kurallarim.source.manual": {"tr": "Manuel", "en": "Manual"},
    "kurallarim.source.correction": {"tr": "Düzeltmeden türetildi", "en": "Derived from a correction"},
    "kurallarim.action.duration": {"tr": "Varsayılan süre", "en": "Default duration"},
    "kurallarim.action.reminder": {"tr": "Hatırlatıcı", "en": "Reminder"},
    "kurallarim.action.importance": {"tr": "Önem", "en": "Importance"},

    # --- Yeni kural formu ---
    "kural_yeni.title": {"tr": "Yeni Kural", "en": "New Rule"},
    "kural_yeni.field.rule_text": {"tr": "Kural (doğal dilde)", "en": "Rule (in plain language)"},
    "kural_yeni.rule_text_hint": {
        "tr": "Bu metin yalnızca gösterim ve arama için kullanılır; aşağıdaki alanlar kuralın gerçekte ne yapacağını belirler.",
        "en": "This text is only for display and search; the fields below determine what the rule actually does.",
    },
    "kural_yeni.field.scope": {"tr": "Kapsam", "en": "Scope"},
    "kural_yeni.scope.global": {"tr": "Tüm etkinlikler", "en": "All events"},
    "kural_yeni.scope.event_type": {"tr": "Belirli bir tür", "en": "A specific type"},
    "kural_yeni.scope.sender": {"tr": "Belirli bir gönderen", "en": "A specific sender"},
    "kural_yeni.field.event_type": {"tr": "Etkinlik türü", "en": "Event type"},
    "kural_yeni.field.sender": {"tr": "Gönderen e-posta adresi", "en": "Sender email address"},
    "kural_yeni.field.action": {"tr": "Ne yapılsın?", "en": "What should happen?"},
    "kural_yeni.field.duration": {"tr": "Varsayılan süre (dakika)", "en": "Default duration (minutes)"},
    "kural_yeni.field.reminder": {"tr": "Hatırlatıcı (dakika önce)", "en": "Reminder (minutes before)"},
    "kural_yeni.field.importance_value": {"tr": "Önem seviyesi", "en": "Importance level"},
    "kural_yeni.submit": {"tr": "Kuralı kaydet", "en": "Save rule"},
    "kural_yeni.error_missing_value": {
        "tr": "Seçtiğiniz eylem için bir değer girmelisiniz.",
        "en": "You must enter a value for the action you selected.",
    },
    "kural_yeni.mode.structured": {"tr": "Yapılandırılmış", "en": "Structured"},
    "kural_yeni.mode.natural": {"tr": "Doğal dilde yaz", "en": "Write in plain language"},
    "kural_yeni.natural.field.rule_text": {
        "tr": "Kuralı kendi cümlelerinizle yazın",
        "en": "Write the rule in your own words",
    },
    "kural_yeni.natural.placeholder": {
        "tr": "örn. Sınavlar her zaman 90 dakika sürsün",
        "en": "e.g. Exams should always be 90 minutes",
    },
    "kural_yeni.natural.hint": {
        "tr": "Model yerelde çalışıyor — bu birkaç saniye sürebilir. Etkinlik türü ve eylem metinden otomatik çıkarılır.",
        "en": "The model runs locally — this may take a few seconds. Event type and action are inferred from the text.",
    },
    "kural_yeni.natural.submit": {"tr": "LLM ile kaydet", "en": "Save with LLM"},
    "kural_yeni.error_llm": {
        "tr": "Bu kuraldan somut bir eylem çıkaramadım — daha net yazmayı deneyin veya yapılandırılmış formu kullanın.",
        "en": "I couldn't extract a concrete action from this rule — try being more specific, or use the structured form.",
    },

    # --- Düzeltmelerim ---
    "duzeltmelerim.title": {"tr": "Düzeltmelerim", "en": "My Corrections"},
    "duzeltmelerim.empty": {
        "tr": "Henüz bir düzeltme kaydı yok.",
        "en": "No corrections recorded yet.",
    },
    "duzeltmelerim.filter.all": {"tr": "Hepsi", "en": "All"},
    "duzeltmelerim.filter.field": {"tr": "Alan düzeltmesi", "en": "Field correction"},
    "duzeltmelerim.filter.classification": {"tr": "Sınıflandırma", "en": "Classification"},
    "duzeltmelerim.field.reminders": {"tr": "Hatırlatıcılar", "en": "Reminders"},
    "duzeltmelerim.no_diff": {
        "tr": "Reddedildi, alan değişikliği yok.",
        "en": "Rejected, no field changes.",
    },
    "duzeltmelerim.classification_note": {
        "tr": "Bu mail hiç takvimlik değildi (sınıflandırma düzeltmesi).",
        "en": "This email wasn't calendar-worthy at all (classification correction).",
    },
    "duzeltmelerim.rule_derived": {"tr": "Türetilen kural", "en": "Derived rule"},
    "duzeltmelerim.no_rule_derived": {"tr": "Kural türetilmedi.", "en": "No rule was derived."},
    "duzeltmelerim.rule_inactive_note": {
        "tr": "(bu kural şu an pasif)",
        "en": "(this rule is currently inactive)",
    },
    "duzeltmelerim.field.scope": {"tr": "Kapsam", "en": "Scope"},
    "duzeltmelerim.scope.single_event": {"tr": "Yalnızca bu etkinlik", "en": "This event only"},
    "duzeltmelerim.scope.event_type": {"tr": "Tür: {value}", "en": "Type: {value}"},
    "duzeltmelerim.scope.sender": {"tr": "Gönderen: {value}", "en": "Sender: {value}"},
    "duzeltmelerim.scope.account": {"tr": "Tüm etkinlikler", "en": "All events"},
    "duzeltmelerim.use_in_future": {"tr": "Gelecekte kullan", "en": "Use in future"},
    "duzeltmelerim.stop_using": {"tr": "Kullanma", "en": "Stop using"},
    "duzeltmelerim.delete": {"tr": "Sil", "en": "Delete"},
    "duzeltmelerim.view_rule": {"tr": "Kurallarım'da gör", "en": "View in My Rules"},
    "duzeltmelerim.field.before": {"tr": "Önce", "en": "Before"},
    "duzeltmelerim.field.after": {"tr": "Sonra", "en": "After"},

    # --- Ayarlar ---
    "ayarlar.title": {"tr": "Ayarlar", "en": "Settings"},
    "ayarlar.section.language": {"tr": "Dil", "en": "Language"},
    "ayarlar.section.timezone": {"tr": "Saat dilimi", "en": "Time zone"},
    "ayarlar.timezone.no_account_hint": {
        "tr": "Saat dilimi hesap başına ayarlanır — önce bir e-posta hesabı bağlayın.",
        "en": "Time zone is set per account — connect an email account first.",
    },
    "ayarlar.field.timezone": {"tr": "Saat dilimi", "en": "Time zone"},
    "ayarlar.timezone.hint": {
        "tr": "Bu ayar yalnızca Takvim ve Ana Sayfa'daki görüntülemeyi etkiler; yeni etkinlikler şu an için varsayılan saat dilimiyle kaydedilir.",
        "en": "This only affects how times are displayed on Calendar and Home; new events are still saved using the default time zone for now.",
    },
    "ayarlar.save": {"tr": "Kaydet", "en": "Save"},
    "ayarlar.section.diagnostics": {"tr": "Tanılama", "en": "Diagnostics"},
    "ayarlar.diagnostics.db_path": {"tr": "Veritabanı dosyası", "en": "Database file"},
    "ayarlar.diagnostics.log_path": {"tr": "Günlük dosyası", "en": "Log file"},
    "ayarlar.diagnostics.chat_model": {"tr": "Sohbet modeli", "en": "Chat model"},
    "ayarlar.diagnostics.embedding_model": {"tr": "Embedding modeli", "en": "Embedding model"},

    # --- Chatbox (bkz. plan "Web Chatbox") ---
    "chat.generic_error": {
        "tr": "Bir sorun oldu, tekrar dener misiniz?",
        "en": "Something went wrong — could you try again?",
    },
    "chat.calendar_error": {
        "tr": "Takvimle konuşurken bir sorun oldu, tekrar dener misiniz?",
        "en": "There was a problem talking to the calendar — could you try again?",
    },
    "chat.attempts_exhausted": {
        "tr": "Bu bilgiyi alamadım, en baştan başlayalım.",
        "en": "I couldn't get this from you — let's start over.",
    },
    "chat.other": {
        "tr": "Bunu tam anlayamadım. Şu an yeni etkinlik eklemek, takviminizi sormak ve mevcut bir etkinliği değiştirmek için kullanılabilirim.",
        "en": "I didn't quite understand that. Right now I can add new events, answer questions about your calendar, and change an existing event.",
    },
    "chat.define_policy.saved": {
        "tr": "Kaydettim: {scope} için '{rule}' kuralı artık aktif.",
        "en": "Saved — the rule '{rule}' is now active for {scope}.",
    },
    "chat.define_policy.no_rule_extracted": {
        "tr": "Bu kuraldan somut bir davranış çıkaramadım — biraz daha net ifade eder misiniz?",
        "en": "I couldn't extract a concrete behavior from that rule — could you phrase it more specifically?",
    },
    "chat.define_policy.extraction_failed": {
        "tr": "Bu kuralı işleyemedim, biraz daha net ifade edip tekrar dener misiniz?",
        "en": "I couldn't process that rule — could you try phrasing it again?",
    },
    "chat.acm.ask_reject_feedback": {
        "tr": "Neden reddettiniz? (atlamak için \"Atla\"ya basabilir ya da boş geçebilirsiniz)",
        "en": "Why did you reject it? (tap Skip or leave it blank to skip)",
    },
    "chat.acm.skip": {"tr": "Atla", "en": "Skip"},
    "chat.acm.skipped": {"tr": "Tamam.", "en": "Okay."},
    "chat.acm.ask_apply_future": {
        "tr": "Bunu gelecekte benzer etkinliklerde de uygulayayım mı?",
        "en": "Should I apply this to similar events in the future too?",
    },
    "chat.acm.ask_apply_future_edit": {
        "tr": "Bu düzenlemeyi gelecekte benzer etkinliklerde de uygulayayım mı?",
        "en": "Should I apply this edit to similar events in the future too?",
    },
    "chat.acm.ask_scope": {
        "tr": "Yalnızca '{event_type}' türü etkinliklerde mi, yoksa her zaman mı?",
        "en": "Only for '{event_type}' events, or always?",
    },
    "chat.acm.scope_event_type_button": {"tr": "Yalnızca bu tür", "en": "Only this type"},
    "chat.acm.scope_always_button": {"tr": "Her zaman", "en": "Always"},
    "chat.acm.scope_event_type_desc": {"tr": "'{event_type}' türü etkinlikler", "en": "'{event_type}' events"},
    "chat.acm.scope_always_desc": {"tr": "tüm etkinlikler", "en": "all events"},
    "chat.acm.saved_policy": {
        "tr": "Kaydettim: {scope} için gelecekte şunu uygulayacağım: {feedback}",
        "en": "Saved — I'll apply this to {scope} going forward: {feedback}",
    },
    "chat.acm.no_rule_extracted": {
        "tr": "Bu düzeltmeden somut bir kural çıkaramadım.",
        "en": "I couldn't turn this into a concrete rule.",
    },
    "chat.acm.extraction_failed": {
        "tr": "Bu düzeltmeyi işleyemedim, kural olarak kaydedemedim.",
        "en": "I couldn't process that correction — it wasn't saved as a rule.",
    },
    "chat.acm.not_saved_as_rule": {
        "tr": "Tamam, bunu bir kural olarak kaydetmedim.",
        "en": "Okay, I didn't save this as a rule.",
    },
    "chat.update.extraction_failed": {
        "tr": "Bu isteği işleyemedim, tekrar ifade eder misiniz?",
        "en": "I couldn't process that request — could you rephrase it?",
    },
    "chat.update.no_matches": {
        "tr": "Değiştirmek istediğiniz etkinliği bulamadım. Daha net tarif eder misiniz (başlık/tarih)?",
        "en": "I couldn't find the event you want to change. Could you describe it more precisely (title/date)?",
    },
    "chat.update.disambiguate_prompt": {
        "tr": "Birden fazla etkinlik buldum, hangisini kastediyorsunuz? Bir numara seçin veya iptal edin (i).",
        "en": "I found more than one matching event — which one did you mean? Pick a number, or cancel (i).",
    },
    "chat.update.cancelled": {"tr": "İptal edildi, hiçbir değişiklik yapılmadı.", "en": "Cancelled — nothing was changed."},
    "chat.update.confirm_delete": {
        "tr": "'{title}' ({when}) etkinliğini SİLMEK üzeresiniz. Onaylıyor musunuz?",
        "en": "You're about to DELETE '{title}' ({when}). Do you confirm?",
    },
    "chat.update.deleted": {"tr": "Etkinlik silindi.", "en": "Event deleted."},
    "chat.update.no_new_time": {
        "tr": "Yeni tarih/saati anlayamadım, tekrar ifade eder misiniz?",
        "en": "I couldn't understand the new date/time — could you rephrase it?",
    },
    "chat.update.confirm_move": {
        "tr": "'{title}' ({old}) -> {new} olarak taşınacak. Onaylıyor musunuz?",
        "en": "'{title}' ({old}) will move to {new}. Do you confirm?",
    },
    "chat.update.move_conflict_warning": {
        "tr": "⚠ Yeni saatte zaten başka bir etkinliğiniz var ({count} çakışma).",
        "en": "⚠ You already have something else at the new time ({count} conflict(s)).",
    },
    "chat.update.updated": {"tr": "Etkinlik güncellendi.", "en": "Event updated."},

    "chat.create.extraction_failed": {
        "tr": "Bu mesajı işleyemedim, tekrar ifade eder misiniz?",
        "en": "I couldn't process that message — could you rephrase it?",
    },
    "chat.create.ask_title": {"tr": "Etkinliğin başlığı ne olsun?", "en": "What should the event be called?"},
    "chat.create.ask_duration": {
        "tr": "Süre ne kadar? (örn: 30, 1 saat)",
        "en": "How long will it take? (e.g. 30, 1 hour)",
    },
    "chat.create.duration_default": {
        "tr": "(Sistem varsayılanı: toplantılar için {minutes} dakika — henüz kendi kuralınızı tanımlamadınız.)",
        "en": "(System default: {minutes} minutes for meetings — you haven't defined your own rule yet.)",
    },
    "chat.create.duration_invalid": {
        "tr": "Anlayamadım, bir sayı içeren şekilde tekrar dener misiniz? (örn: 45 veya '1 saat')",
        "en": "I didn't catch that — could you include a number? (e.g. 45 or '1 hour')",
    },
    "chat.create.ask_start_datetime": {
        "tr": "Tarih/saat (YYYY-MM-DDTHH:MM:SS)?",
        "en": "Date/time (YYYY-MM-DDTHH:MM:SS)?",
    },
    "chat.create.start_datetime_invalid": {
        "tr": "Bu formatı anlayamadım, YYYY-MM-DDTHH:MM:SS biçiminde tekrar dener misiniz?",
        "en": "I couldn't parse that — could you use the YYYY-MM-DDTHH:MM:SS format?",
    },
    "chat.create.ask_ambiguous_time": {
        "tr": "Saat belirsiz görünüyor — tam olarak kaçta? (örn: 13:00)",
        "en": "The time seems unclear — what time exactly? (e.g. 13:00)",
    },
    "chat.create.ambiguous_time_invalid": {
        "tr": "Saati anlayamadım, tekrar dener misiniz? (örn: 13:00, 13.30, 'saat 9')",
        "en": "I couldn't parse that time — could you try again? (e.g. 13:00, 1pm)",
    },
    "chat.create.conflict_no_alternatives": {
        "tr": "{start}-{end} aralığında zaten bir etkinliğiniz var ve yakın zamanda uygun bir alternatif bulamadım. Yine de bu saatte devam edelim mi?",
        "en": "You already have something at {start}-{end} and I couldn't find a nearby alternative. Should we go ahead at this time anyway?",
    },
    "chat.create.conflict_found": {
        "tr": "⚠ Çakışma bulundu: {start}-{end} aralığında zaten bir etkinliğiniz var.",
        "en": "⚠ Conflict found: you already have something at {start}-{end}.",
    },
    "chat.create.alternatives_intro": {
        "tr": "Alternatif uygun saatler (bir numara seçin, yine de bu saatte devam edin (d), veya iptal edin (i)):",
        "en": "Alternative times (pick a number, keep this time anyway (d), or cancel (i)):",
    },
    "chat.create.cancelled_conflict": {"tr": "İptal edildi, takvime yazılmadı.", "en": "Cancelled — nothing was added to the calendar."},
    "chat.create.preview_header": {"tr": "Önizleme", "en": "Preview"},
    "chat.create.conflict_label": {"tr": "Çakışma", "en": "Conflict"},
    "chat.create.conflict.none": {"tr": "Yok", "en": "None"},
    "chat.create.conflict.kept_anyway": {"tr": "Var (yine de devam edildi)", "en": "Yes (kept anyway)"},
    "chat.create.conflict.unresolved": {"tr": "Var (çözülmedi)", "en": "Yes (unresolved)"},
    "chat.create.conflict.moved": {"tr": "Vardı, taşındı", "en": "There was one, moved"},
    "chat.create.conflict.cancelled": {"tr": "Var (iptal edilecek)", "en": "Yes (will be cancelled)"},
    "chat.create.reminder_item": {"tr": "{minutes} dk önce", "en": "{minutes} min before"},
    "chat.create.approved": {"tr": "Takvime eklendi.", "en": "Added to your calendar."},
    "chat.create.rejected": {"tr": "Reddedildi, takvime yazılmadı.", "en": "Rejected — nothing was added to the calendar."},
    "chat.create.preview_unrecognized": {
        "tr": "Bunu anlayamadım — onaylamak için \"evet\", değiştirmek için \"düzenle\", vazgeçmek için "
        "\"hayır\" yazabilir ya da aşağıdaki butonları kullanabilirsiniz.",
        "en": "I didn't understand that — reply \"yes\" to approve, \"edit\" to change something, "
        "\"no\" to cancel, or use the buttons below.",
    },
    "chat.create.edit_pick_field_prompt": {
        "tr": "Hangi alanı düzenlemek istersiniz? (başlık/saat/süre/önem/konum)",
        "en": "Which field would you like to edit? (title/time/duration/importance/location)",
    },
    "chat.create.edit_field_not_understood": {"tr": "Anlamadım, hiçbir şey değiştirilmedi.", "en": "I didn't understand — nothing was changed."},
    "chat.create.edit_ask.title": {"tr": "Yeni başlık:", "en": "New title:"},
    "chat.create.edit_ask.start_datetime": {
        "tr": "Yeni tarih/saat (YYYY-MM-DDTHH:MM:SS):",
        "en": "New date/time (YYYY-MM-DDTHH:MM:SS):",
    },
    "chat.create.edit_ask.duration_minutes": {"tr": "Yeni süre (örn: 30, 1 saat):", "en": "New duration (e.g. 30, 1 hour):"},
    "chat.create.edit_ask.importance": {"tr": "Yeni önem (low/normal/high):", "en": "New importance (low/normal/high):"},
    "chat.create.edit_ask.location": {"tr": "Yeni konum:", "en": "New location:"},
    "chat.create.edit_importance_invalid": {
        "tr": "Geçersiz değer (low/normal/high olmalı), önem değiştirilmedi.",
        "en": "Invalid value (must be low/normal/high) — importance wasn't changed.",
    },

    "chat.query.no_range": {
        "tr": "Hangi tarih aralığını merak ediyorsunuz, tam olarak söyler misiniz?",
        "en": "Which date range did you mean, exactly?",
    },
    "chat.query.no_events": {
        "tr": "{start} - {end} arasında hiç etkinliğiniz yok.",
        "en": "You have no events between {start} and {end}.",
    },
    "chat.query.events_found": {"tr": "{count} etkinliğiniz var:", "en": "You have {count} event(s):"},
    "chat.query.all_day": {"tr": "tüm gün", "en": "all day"},

    # --- Chatbox: arayüz (bkz. src/ui/templates/partials/_asistan_chat.html) ---
    "chat.placeholder": {"tr": "Bir şey yazın...", "en": "Type a message..."},
    "chat.send": {"tr": "Gönder", "en": "Send"},
    "chat.new_chat": {"tr": "Yeni sohbet", "en": "New chat"},
    "chat.empty_hint": {
        "tr": "Bir etkinlik oluşturmamı, takvimini sorgulamamı ya da bir etkinliği güncellememi isteyebilirsin.",
        "en": "You can ask me to create an event, look up your calendar, or update an event.",
    },
    "chat.yes": {"tr": "Evet", "en": "Yes"},
    "chat.no": {"tr": "Hayır", "en": "No"},
    "chat.create.keep_anyway": {"tr": "Yine de devam et", "en": "Keep it anyway"},

    # --- Enum: Importance ---
    "enum.importance.low": {"tr": "Düşük", "en": "Low"},
    "enum.importance.normal": {"tr": "Normal", "en": "Normal"},
    "enum.importance.high": {"tr": "Yüksek", "en": "High"},

    # --- Enum: EventType ---
    "enum.event_type.meeting": {"tr": "Toplantı", "en": "Meeting"},
    "enum.event_type.appointment": {"tr": "Randevu", "en": "Appointment"},
    "enum.event_type.exam": {"tr": "Sınav", "en": "Exam"},
    "enum.event_type.deadline": {"tr": "Son Tarih", "en": "Deadline"},
    "enum.event_type.travel": {"tr": "Seyahat", "en": "Travel"},
    "enum.event_type.reservation": {"tr": "Rezervasyon", "en": "Reservation"},
    "enum.event_type.personal_commitment": {"tr": "Kişisel", "en": "Personal"},
    "enum.event_type.other": {"tr": "Diğer", "en": "Other"},
}
