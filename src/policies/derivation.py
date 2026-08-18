"""Doğal dil kural metnini yapılandırılmış bir PersonalPolicy'ye çevirir
(bkz. docs/architecture-plan.md §9/§10).

Hem doğrudan kural tanımlama (src/services/vertical_prototype.py::handle_define_policy)
hem de Adaptive Correction Memory (src/memory/correction_memory.py) aynı
LLM-çıktısı-şeklini kullanır; bu modül o mantığı tek yerde tutar."""

from __future__ import annotations

from src.core.models import PersonalPolicy, PolicySource
from src.policies.store import add_policy, deactivate_policy, find_active_conflicting_policy
from src.providers.base import EmbeddingProvider, LLMProvider
from src.providers.json_generation import generate_json
from src.rag.policy_retrieval import embed_and_store_policy

VALID_IMPORTANCE_VALUES = {"low", "normal", "high"}


def _policy_extraction_system_prompt() -> str:
    return (
        "Sen bir takvim asistanısın. Kullanıcı doğal dilde kişisel bir kural "
        "tanımlıyor. Bunu yapılandırılmış hale getir.\n"
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme. Alanlar:\n"
        '{"event_type": "meeting|appointment|exam|deadline|travel|reservation|'
        'personal_commitment|other" veya null (kural TÜM etkinlik türleri için '
        "geçerliyse null bırak), "
        '"default_duration_minutes": integer veya null, '
        '"reminder_minutes_before": integer veya null, '
        '"importance": "low|normal|high" veya null}\n'
        "Yalnızca kuralda AÇIKÇA belirtilen alanı doldur, diğerlerini null bırak — uydurma."
    )


def derive_and_save_policy(
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    rule_text: str,
    event_type: str | None = None,
    sender: str | None = None,
    infer_event_type: bool = True,
    source: PolicySource = PolicySource.MANUAL,
) -> PersonalPolicy | None:
    """Kural metninden yapılandırılmış bir politika türetir, kaydeder, embed eder.

    ``infer_event_type=True`` (varsayılan, manuel kural tanımlama): ``event_type``
    verilmemişse LLM'in kural metninden çıkardığı değere güvenilir.
    ``infer_event_type=False`` (ACM, kullanıcı scope'u açıkça seçtiğinde):
    ``event_type`` AYNEN kullanılır (None ise kasıtlı olarak global demektir),
    LLM'in metinden tahmin ettiği değer YOK SAYILIR.

    ``sender`` verilirse politika sender-scope'lu olur ve ``event_type``'tan
    (verilmiş olsa bile) ÖNCELİKLİDİR — bir düzeltme aynı anda hem "bu tür
    etkinliklerde" hem "bu göndericiden gelenlerde" olamaz (bkz. §9 scope
    hiyerarşisi, kullanıcı tek bir kapsam seçer).

    Aynı `category`+kapsam (event_type/sender/global) kombinasyonunda zaten
    aktif bir politika varsa, yenisi oluşturulduktan sonra eskisi
    `deactivate_policy` ile versiyonlanır (§9 çelişki tespiti/versiyonlama) —
    aynı kuralın iki kez tanımlanması artık iki ayrı aktif politika
    biriktirmiyor.

    Kuraldan somut bir davranış (`structured_action`) çıkarılamazsa None döner.
    """
    fields = generate_json(llm, _policy_extraction_system_prompt(), rule_text)

    # importance için model bazen "low|normal|high" seçenek listesini olduğu
    # gibi döndürüyor (event_type'ta görülen aynı hata sınıfı, bkz.
    # src/services/extraction.py _coerce_event_type) — geçersiz değeri burada,
    # politika kaydedilmeden önce ele alıyoruz ki daha sonra (politika
    # uygulanırken) candidate.importance atamasında çökmesin.
    if fields.get("importance") not in VALID_IMPORTANCE_VALUES:
        fields["importance"] = None

    structured_action = {
        k: fields[k]
        for k in ("default_duration_minutes", "reminder_minutes_before", "importance")
        if fields.get(k) is not None
    }
    if not structured_action:
        return None

    category = next(iter(structured_action))
    effective_event_type = event_type if not infer_event_type else (event_type or fields.get("event_type"))
    if sender:
        effective_event_type = None  # sender daha spesifik, event_type'ı ezer

    conflict = find_active_conflicting_policy(category, event_type=effective_event_type, sender=sender)

    policy = add_policy(
        category=category,
        natural_language_rule=rule_text,
        structured_action=structured_action,
        event_type=effective_event_type,
        sender=sender,
        source=source,
    )
    embed_and_store_policy(embedding_provider, policy)

    if conflict is not None:
        deactivate_policy(conflict)

    return policy
