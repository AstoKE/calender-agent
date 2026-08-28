"""RAG / Retrieval Layer — kişisel politika retrieval'ı (bkz. docs/architecture-plan.md §9).

Brute-force cosine similarity (program dokümanındaki find_relevant()
yaklaşımıyla uyumlu) — kişisel veri ölçeğinde özel bir vector DB gerekmez.
RAG burada yalnızca "ilgili politikaları getirir"; hangi değerin
uygulanacağına karar vermek Rule Engine'in işi (bkz. §11), burada değil.
"""

from __future__ import annotations

import numpy as np

from src.core.models import PersonalPolicy
from src.policies.store import get_active_policies_for_sender, row_to_policy
from src.providers.base import EmbeddingProvider
from src.storage.db import get_connection


def _serialize_embedding(vector: list[float]) -> bytes:
    return np.array(vector, dtype=np.float32).tobytes()


def _deserialize_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def embed_and_store_policy(embedding_provider: EmbeddingProvider, policy: PersonalPolicy) -> None:
    vector = embedding_provider.embed([policy.natural_language_rule])[0]
    created_at = policy.created_at
    created_at_str = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO policy_embeddings (policy_id, embedding, model_name, dim, created_at) "
            "VALUES (?,?,?,?,?)",
            (
                policy.policy_id,
                _serialize_embedding(vector),
                embedding_provider.model_name,
                len(vector),
                created_at_str,
            ),
        )


def semantic_search_policies(
    embedding_provider: EmbeddingProvider, query_text: str, top_k: int = 5, user_id: str | None = None
) -> list[tuple[PersonalPolicy, float]]:
    """Aktif politikalar arasında query_text'e en benzer top_k'yı, benzerlik
    skoruyla (yüksek = daha alakalı) birlikte döner. ``user_id`` verilirse
    yalnızca o kullanıcının politikaları aranır (bkz. plan "Per-user
    isolation")."""
    query_vector = np.array(embedding_provider.embed([query_text])[0], dtype=np.float32)

    sql = """
        SELECT p.*, e.embedding
        FROM personal_policies p
        JOIN policy_embeddings e ON e.policy_id = p.policy_id
        WHERE p.active = 1 AND e.model_name = ?
    """
    params: list = [embedding_provider.model_name]
    if user_id is not None:
        sql += " AND p.user_id = ?"
        params.append(user_id)

    with get_connection() as conn:
        # e.model_name filtresi kritik: farklı bir embedding modeli/varyantı
        # (örn. CPU<->GPU execution provider değişimi, bkz. providers/foundry_local.py)
        # farklı bir vektör uzayı üretir — eşleşmeyen model_name'li embedding'leri
        # query_vector ile karşılaştırmak anlamsız/yanıltıcı bir benzerlik skoru verir.
        rows = conn.execute(sql, params).fetchall()

    scored: list[tuple[PersonalPolicy, float]] = []
    for row in rows:
        vec = _deserialize_embedding(row["embedding"])
        denom = (np.linalg.norm(query_vector) * np.linalg.norm(vec)) + 1e-9
        similarity = float(np.dot(query_vector, vec) / denom)
        scored.append((row_to_policy(row), similarity))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def retrieve_policies_for_event(
    embedding_provider: EmbeddingProvider, event_type: str, sender: str | None = None, top_k: int = 5,
    user_id: str | None = None,
) -> list[PersonalPolicy]:
    """event_type'a (ve varsa gönderene) göre en alakalı politikaları getirir.
    ``user_id`` verilirse yalnızca o kullanıcının politikaları (bkz. plan
    "Per-user isolation") — ``None`` ise (CLI) eski, sahiplikten bağımsız
    davranış.

    Sender-scope'lu politikalar semantik aramaya BIRAKILMIYOR: bir düzeltme
    metni ("LinkedIn'den gelenlere düşük önem ver") event_type açıklamasına
    ("etkinlik türü: meeting") anlamsal olarak hiç benzemeyebilir, top_k*2
    aday havuzuna hiç girmeyebilirdi. Gönderen adresi zaten bilindiği için
    (mailden geliyorsa) bu deterministik bir eşleşmedir — RAG'a değil doğrudan
    koda bırakılır (bkz. §11), ve en spesifik kural olduğu için sonuçların
    başına konur (bkz. §9 politika önceliği: daha spesifik kural önce gelir).
    """
    sender_matches = get_active_policies_for_sender(sender, user_id=user_id) if sender else []

    query_text = f"etkinlik türü: {event_type}"
    candidates = semantic_search_policies(embedding_provider, query_text, top_k=top_k * 2, user_id=user_id)
    # Sender-scope'lu politikalar semantik havuzdan çıkarılıyor: bunlar SADECE
    # yukarıdaki deterministik sender_matches üzerinden gelmeli. Aksi halde bir
    # politikanın metni ("LinkedIn'den gelenlere düşük önem ver") event_type
    # sorgusuyla tesadüfen benzer çıkıp, göndereni hiç eşleşmeyen bir candidate'a
    # da uygulanabilirdi (canlı testte doğrulandı — gerçek bir sızıntıydı).
    candidates = [item for item in candidates if "sender" not in item[0].structured_conditions]
    # AYNI sızıntı event_type-scope'lu politikalar için de vardı (canlı testte
    # bulundu): "sınav tarihlerinin anımsatıcıları son 3 gün öncesinden olsun"
    # (event_type=exam) gibi bir politika, "hatırlatıcı" kavramına semantik
    # olarak benzediği için toplantı/seyahat/diğer gibi TAMAMEN alakasız
    # candidate'lara da uygulanıyordu — sort_key aşağıda yalnızca exact_match'i
    # ÖNE alıyordu, ELEMİYORDU. Global (event_type koşulu YOK) politikalar
    # her zaman uygulanabilir kalır; BAŞKA bir event_type'a scope'lanmış bir
    # politika bu candidate için tamamen elenir.
    candidates = [
        item for item in candidates
        if item[0].structured_conditions.get("event_type") in (None, event_type)
    ]

    def sort_key(item: tuple[PersonalPolicy, float]) -> tuple[bool, int, float]:
        policy, similarity = item
        exact_match = policy.structured_conditions.get("event_type") == event_type
        return (exact_match, policy.priority, similarity)

    ranked = sorted(candidates, key=sort_key, reverse=True)

    seen_ids: set[str] = set()
    merged: list[PersonalPolicy] = []
    for policy in [*sender_matches, *[p for p, _ in ranked]]:
        if policy.policy_id in seen_ids:
            continue
        seen_ids.add(policy.policy_id)
        merged.append(policy)
    return merged[:top_k]
