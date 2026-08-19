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
        "tr": "Henüz bağlı bir hesap yok. CLI üzerinden bir hesap ekleyin.",
        "en": "No connected accounts yet. Add one from the CLI.",
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
