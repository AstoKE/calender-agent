"""Mail analizi: takvimlik sınıflandırma + candidate çıkarımı (bkz. docs/architecture-plan.md §4).

Aynı etkinliğe ait maillerin ilişkilendirilmesi (§9C, thread/semantic
correlation) burada YOK — her mail şu an bağımsız değerlendiriliyor; bu
sonraki bir iyileştirme (Adaptive Correction Memory ile birlikte ele
alınacak, bkz. proje task listesi).
"""

from __future__ import annotations

import re
from datetime import datetime

from src.core.logging_config import get_logger
from src.core.models import CandidateEvent, SourceType, UnifiedEmail
from src.providers.base import EmbeddingProvider, LLMProvider
from src.providers.json_generation import generate_json
from src.rag.correction_retrieval import retrieve_similar_classification_corrections
from src.services.extraction import build_candidate_from_fields
from src.services.timeutil import DEFAULT_TIMEZONE, ensure_timezone

BODY_PREVIEW_MAX_CHARS = 1500

logger = get_logger("mail_analysis")

_LONG_URL_RE = re.compile(r"https?://\S{60,}")


def _collapse_long_urls(text: str) -> str:
    """Pazarlama/bildirim mailleri (LinkedIn iş ilanları dahil) genelde
    500+ karakterlik tracking URL'leri içeriyor — bunlar modele hiçbir ek
    bilgi katmıyor ama token bütçesini tüketip, model URL'yi olduğu gibi
    (örn. online_meeting_url'e) kopyalamaya çalışırken JSON çıktısının
    yarıda kesilmesine yol açıyordu (canlı testte görüldü, tekrarlayan
    "Unterminated string" hatası — aynı mail her denemede aynı noktada
    kesiliyordu)."""
    return _LONG_URL_RE.sub("[link]", text)


def build_email_text(email: UnifiedEmail) -> str:
    """Konu/gönderen/içeriği tek bir metinde birleştirir — hem LLM promptlarında
    hem embedding'lerde (bkz. rag/correction_retrieval.py) kullanılıyor, aynı
    temsil hem sınıflandırma anında hem geçmiş düzeltme kaydında kullanılmalı
    ki benzerlik karşılaştırması anlamlı olsun."""
    return (
        f"Konu: {email.subject}\nGönderen: {email.sender}\n"
        f"İçerik:\n{_collapse_long_urls((email.body_text or '')[:BODY_PREVIEW_MAX_CHARS])}"
    )

# Gmail bu kategorileri kendisi atıyor (bkz. Gmail'in Promotions/Social sekmeleri).
# Ölçümde LLM sınıflandırması bu tür açık pazarlama maillerinde bile yanlış
# pozitif üretebiliyordu (örn. bir kurs reklamını "sınav randevusu" sandı) —
# burada deterministik bir ön filtre olarak kullanılıyor (bkz. §11 "kritik
# kararlar için deterministik kod" ilkesi), LLM çağrısı hiç yapılmıyor.
NON_CALENDAR_GMAIL_CATEGORIES = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"}


def _classification_system_prompt() -> str:
    return (
        "Sen bir takvim asistanısın. Bir e-postanın takvime eklenmeye değer bir "
        "bilgi içerip içermediğini belirle.\n"
        "TAKVİME DEĞER — kullanıcının belirli bir TARİH/SAATTE KATILMASI, "
        "GİTMESİ veya bir işlemi YAPMASI gereken somut, kişisel bir zorunluluk "
        "varsa: toplantı daveti, randevu, sınav, ödev/proje teslim tarihi, "
        "başvuru son tarihi, rezervasyon, katılınması gereken etkinlik/ders/"
        "webinar, kullanıcının verdiği bir taahhüt (örn. 'raporu cumaya kadar "
        "göndereceğim'), mevcut bir etkinliğin tarih/saat/yer/bağlantı "
        "değişikliği.\n"
        "DEĞER DEĞİL (ŞÜPHEDE KALIRSAN False DE):\n"
        "- İŞLEM/DURUM BİLDİRİMLERİ: fatura/makbuz hazır, ödeme alındı/"
        "onaylandı, sipariş oluşturuldu/kargoya verildi/teslim edildi, hesap "
        "özeti, abonelik yenilendi — içinde bir tarih GEÇSE BİLE (fatura "
        "tarihi, işlem tarihi, tahmini teslimat tarihi) kullanıcının o "
        "tarihte YAPMASI gereken hiçbir şey yoktur, sadece tamamlanmış bir "
        "işlemin kaydıdır.\n"
        "- Kurs/ürün/hizmet TANITIMI veya SATIN ALMA teklifi — bir tarihten, "
        "sınavdan veya konudan bahsetse BİLE (örn. 'AWS sınavına hazırlanmak "
        "ister misiniz?' bir kurs reklamıdır, gerçek bir sınav randevusu DEĞİL).\n"
        "- Blog yazısı / haber bülteni / bilgilendirici içerik — konusu bir "
        "tarih veya olayla ilgili olsa BİLE (örn. bir gök olayı HAKKINDA yazı), "
        "kullanıcının kendisinin katılması/yapması gereken bir şey değilse.\n"
        "- Genel reklam, kampanya, indirim duyurusu, yinelenen/tekrar mailler, "
        "aksiyon gerektirmeyen bilgilendirme, yalnızca geçmiş bir tarihten "
        "bahseden içerik, otomatik sistem bildirimi/güvenlik uyarısı.\n"
        "- İŞ İLANI/KARİYER FIRSATI BİLDİRİMLERİ (LinkedIn 'İş İlanı "
        "Uyarıları', Kariyer.net vb.): 'X şirketinde Y pozisyonu' türü "
        "bildirimler yalnızca bir fırsatı DUYURUR — mailde AÇIKÇA bir "
        "başvuru son tarihi, mülakat randevusu veya benzeri somut bir "
        "tarih/saat YOKSA false. Pozisyon/şirket/konum bilgisi olması TEK "
        "BAŞINA takvime değer katmaz; kullanıcı henüz başvurmamıştır, "
        "yapması gereken zamana bağlı bir şey yoktur.\n"
        "Örnekler:\n"
        "- 'Proje toplantısı yarın 14:00, Zoom linki ektedir.' -> true "
        "(toplantı daveti)\n"
        "- 'Vize sınavınız 12 Mart 10:00, D-102.' -> true (sınav)\n"
        "- 'Ödev teslim tarihi: 20 Mart 23:59.' -> true (teslim tarihi)\n"
        "- 'Toplantımız perşembe 15:00'e alındı.' -> true (mevcut etkinlik "
        "değişikliği)\n"
        "- 'IKS2026001102391 numaralı e-Arşiv faturanız hazırdır.' -> false "
        "(işlem bildirimi — kullanıcının o tarihte yapması gereken bir şey "
        "yok)\n"
        "- 'Siparişiniz kargoya verildi, tahmini teslimat 14 Ağustos.' -> "
        "false (işlem bildirimi, teslimat tarihinde kullanıcının katılması "
        "gereken bir şey yok)\n"
        "- 'Yeni AI kursumuz başlıyor, hemen kaydolun!' -> false (reklam)\n"
        "- 'Amadeus şirketinde Junior Software Development Engineer "
        "pozisyonu ilginizi çekebilir.' -> false (iş ilanı bildirimi, "
        "belirli bir tarih/saat yok)\n"
        "- 'Başvurunuzu değerlendirdik, mülakatınız 25 Ağustos 14:00'te.' "
        "-> true (somut bir randevu/tarih var)\n"
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme:\n"
        '{"is_calendar_worthy": true veya false, "reason": "kısa gerekçe (tek cümle)"}'
    )


def is_calendar_worthy(
    llm: LLMProvider, embedding_provider: EmbeddingProvider, email: UnifiedEmail
) -> tuple[bool, str]:
    matched_categories = NON_CALENDAR_GMAIL_CATEGORIES & set(email.labels)
    if matched_categories:
        logger.debug(
            "is_calendar_worthy: deterministic filter matched for %r (labels=%s)",
            email.subject, email.labels,
        )
        return False, f"Gmail bunu {', '.join(sorted(matched_categories))} kategorisine ayırmış."

    user_prompt = build_email_text(email)

    # Kullanıcının "bu mail hiç takvimlik değildi" diye düzelttiği geçmiş
    # örneklere semantik olarak benzeyen varsa, bunlar context_chunks olarak
    # eklenir (bkz. rag/correction_retrieval.py, Adaptive Correction Memory
    # §10) — RAG burada da bir "karar" vermiyor, sadece ilgili geçmiş
    # düzeltmeyi bağlam olarak sunuyor, son kararı yine LLM+kullanıcı onayı
    # veriyor (bkz. §11).
    context_chunks = retrieve_similar_classification_corrections(embedding_provider, user_prompt, top_k=3)

    # allow_thinking=True: /no_think ile hızlı ama canlı testte gözlenen yanlış
    # pozitifler (örn. tarih içermeyen iş ilanlarını takvimlik sanma) çok daha
    # sık çıkıyordu; GPU'da thinking'in maliyeti (~3-7sn) artık tolere edilebilir
    # ve doğruluğu belirgin şekilde iyileştiriyor (bkz. json_generation.py notu).
    data = generate_json(
        llm, _classification_system_prompt(), user_prompt,
        context_chunks=context_chunks or None, allow_thinking=True,
    )
    worthy, reason = bool(data.get("is_calendar_worthy")), data.get("reason", "")
    logger.info("is_calendar_worthy: %r -> worthy=%s reason=%r labels=%s", email.subject, worthy, reason, email.labels)
    return worthy, reason


def _event_extraction_system_prompt(today: datetime) -> str:
    return (
        "Sen bir takvim asistanısın. Aşağıdaki e-postadan bir etkinlik bilgisi çıkar.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Göreceli ifadeleri bu tarihe göre çöz.\n"
        "KRİTİK KURALLAR:\n"
        "1. Mailde AÇIKÇA belirtilmeyen hiçbir bilgiyi UYDURMA — konum, kişi, "
        "bağlantı gibi alanlar mailde yoksa null bırak.\n"
        "2. AMBIGUOUS_FIELDS YALNIZCA SAATİN KENDİSİ NET DEĞİLSE kullanılır — "
        "örn. mailde 'sabah', 'öğleden sonra' gibi belirsiz bir ifade var ya "
        "da hiç saat yok. Mailde 'saat 15:00', '15.00'te', 'saat 9' gibi NET "
        "bir saat açıkça belirtiliyorsa, bunu ambiguous SAYMA — tarihin "
        "göreceli bir ifade olması ('yarın', 'gelecek hafta' gibi, bunu "
        "yukarıdaki 'bugünün tarihi'ne göre kendin hesaplaman gerekmesi) TEK "
        "BAŞINA belirsizlik değildir, doğru hesapladıktan sonra "
        "ambiguous_fields'e ekleme.\n"
        "3. Emin olmadığın her alan için tahmin yerine null + ambiguous_fields tercih et.\n"
        "4. Mailde HİÇBİR tarih/saat ifadesi yoksa (örn. bir iş ilanı sadece "
        "pozisyon/şirket bilgisi veriyorsa) start_datetime'ı yukarıdaki "
        "'bugünün tarihi'yle DOLDURMA — bu yalnızca göreceli ifadeleri "
        "(örn. 'yarın', 'gelecek hafta') çözmek içindir, mailde tarih yoksa "
        "sonuç null olmalı ve ambiguous_fields'e eklenmelidir.\n"
        "SADECE geçerli JSON döndür. Alanlar:\n"
        '{"event_type": "meeting|appointment|exam|deadline|travel|reservation|'
        'personal_commitment|other", '
        '"title": string veya null, '
        '"start_datetime": "YYYY-MM-DDTHH:MM:SS" veya null, '
        '"duration_minutes": integer veya null, '
        '"location": string veya null, '
        '"online_meeting_url": string veya null, '
        '"ambiguous_fields": [string]}'
    )


def extract_candidate_from_email(llm: LLMProvider, email: UnifiedEmail) -> CandidateEvent:
    today = datetime.now().astimezone()
    user_prompt = build_email_text(email)
    fields = generate_json(llm, _event_extraction_system_prompt(today), user_prompt)

    # Prompttaki kural #4'e rağmen model bazen mailde hiç tarih olmadığında
    # sistem promptundaki "bugünün tarihi" referansını start_datetime'a aynen
    # kopyalıyor (canlı testte görüldü, mikrosaniye hassasiyetiyle "şu an") —
    # talimata güvenmek yerine deterministik bir son kontrol: dönen değer
    # "bugün" referansına birkaç dakikadan yakınsa, gerçek bir mailden
    # çıkarılmış değer olma ihtimali neredeyse sıfırdır (bkz. §11: kritik
    # doğrulama LLM'e değil koda bırakılır).
    raw_start = fields.get("start_datetime")
    if raw_start:
        try:
            # ensure_timezone: model bazen offset'siz (naive) bir saat döndürüyor
            # (canlı testte görüldü) — "today" (aware) ile doğrudan çıkarma
            # TypeError'a yol açardı; naive değeri DEFAULT_TIMEZONE varsayarak
            # karşılaştırılabilir hale getiriyoruz (bkz. timeutil.ensure_timezone
            # docstring'i, aynı desen candidate.start_datetime için de kullanılıyor).
            if abs((ensure_timezone(raw_start) - today).total_seconds()) < 120:
                fields["start_datetime"] = None
                ambiguous = set(fields.get("ambiguous_fields") or [])
                ambiguous.add("start_datetime")
                fields["ambiguous_fields"] = list(ambiguous)
        except ValueError:
            pass

    candidate = build_candidate_from_fields(
        fields,
        source_type=SourceType.EMAIL,
        source_references=[email.message_id],
        source_languages=[email.detected_language] if email.detected_language else [],
        extraction_reason=f'Mailden çıkarıldı: "{email.subject}" ({email.sender})',
    )
    logger.debug(
        "extract_candidate_from_email: %r -> event_type=%s missing=%s ambiguous=%s",
        email.subject, candidate.event_type, candidate.missing_fields, candidate.ambiguous_fields,
    )
    return candidate
