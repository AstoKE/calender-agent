"""Mail analizi: takvimlik sınıflandırma + candidate çıkarımı (bkz. docs/architecture-plan.md §4).

Aynı etkinliğe ait maillerin ilişkilendirilmesi (§9C, thread/semantic
correlation) burada YOK — her mail şu an bağımsız değerlendiriliyor; bu
sonraki bir iyileştirme (Adaptive Correction Memory ile birlikte ele
alınacak, bkz. proje task listesi).
"""

from __future__ import annotations

from datetime import datetime

from src.core.logging_config import get_logger
from src.core.models import CandidateEvent, SourceType, UnifiedEmail
from src.providers.base import LLMProvider
from src.providers.json_generation import generate_json
from src.services.extraction import build_candidate_from_fields
from src.services.timeutil import DEFAULT_TIMEZONE

BODY_PREVIEW_MAX_CHARS = 1500

logger = get_logger("mail_analysis")

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
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme:\n"
        '{"is_calendar_worthy": true veya false, "reason": "kısa gerekçe (tek cümle)"}'
    )


def is_calendar_worthy(llm: LLMProvider, email: UnifiedEmail) -> tuple[bool, str]:
    matched_categories = NON_CALENDAR_GMAIL_CATEGORIES & set(email.labels)
    if matched_categories:
        logger.debug(
            "is_calendar_worthy: deterministic filter matched for %r (labels=%s)",
            email.subject, email.labels,
        )
        return False, f"Gmail bunu {', '.join(sorted(matched_categories))} kategorisine ayırmış."

    user_prompt = (
        f"Konu: {email.subject}\nGönderen: {email.sender}\n"
        f"İçerik:\n{(email.body_text or '')[:BODY_PREVIEW_MAX_CHARS]}"
    )
    data = generate_json(llm, _classification_system_prompt(), user_prompt)
    worthy, reason = bool(data.get("is_calendar_worthy")), data.get("reason", "")
    logger.info("is_calendar_worthy: %r -> worthy=%s reason=%r labels=%s", email.subject, worthy, reason, email.labels)
    return worthy, reason


def _event_extraction_system_prompt() -> str:
    today = datetime.now().astimezone()
    return (
        "Sen bir takvim asistanısın. Aşağıdaki e-postadan bir etkinlik bilgisi çıkar.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Göreceli ifadeleri bu tarihe göre çöz.\n"
        "KRİTİK KURALLAR:\n"
        "1. Mailde AÇIKÇA belirtilmeyen hiçbir bilgiyi UYDURMA — konum, kişi, "
        "bağlantı gibi alanlar mailde yoksa null bırak.\n"
        "2. Saat belirsizse (sabah/öğleden sonra netliği yok): tarihi yine de "
        "doğru hesapla, saat için en olası tahmini kullan, AYRICA "
        'ambiguous_fields listesine "start_datetime" ekle.\n'
        "3. Emin olmadığın her alan için tahmin yerine null + ambiguous_fields tercih et.\n"
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
    user_prompt = (
        f"Konu: {email.subject}\nGönderen: {email.sender}\n"
        f"İçerik:\n{(email.body_text or '')[:BODY_PREVIEW_MAX_CHARS]}"
    )
    fields = generate_json(llm, _event_extraction_system_prompt(), user_prompt)
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
