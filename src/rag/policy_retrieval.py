"""RAG / Retrieval Layer — kişisel politika retrieval'ı (bkz. docs/architecture-plan.md §9).

Brute-force cosine similarity (program dokümanındaki find_relevant()
yaklaşımıyla uyumlu) — kişisel veri ölçeğinde özel bir vector DB gerekmez.
RAG burada yalnızca "ilgili politikaları getirir"; hangi değerin
uygulanacağına karar vermek Rule Engine'in işi (bkz. §11), burada değil.
"""

from __future__ import annotations

import numpy as np

from src.core.models import PersonalPolicy
from src.policies.store import row_to_policy
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
    embedding_provider: EmbeddingProvider, query_text: str, top_k: int = 5
) -> list[tuple[PersonalPolicy, float]]:
    """Aktif politikalar arasında query_text'e en benzer top_k'yı, benzerlik
    skoruyla (yüksek = daha alakalı) birlikte döner."""
    query_vector = np.array(embedding_provider.embed([query_text])[0], dtype=np.float32)

    with get_connection() as conn:
        # e.model_name filtresi kritik: farklı bir embedding modeli/varyantı
        # (örn. CPU<->GPU execution provider değişimi, bkz. providers/foundry_local.py)
        # farklı bir vektör uzayı üretir — eşleşmeyen model_name'li embedding'leri
        # query_vector ile karşılaştırmak anlamsız/yanıltıcı bir benzerlik skoru verir.
        rows = conn.execute(
            """
            SELECT p.*, e.embedding
            FROM personal_policies p
            JOIN policy_embeddings e ON e.policy_id = p.policy_id
            WHERE p.active = 1 AND e.model_name = ?
            """,
            (embedding_provider.model_name,),
        ).fetchall()

    scored: list[tuple[PersonalPolicy, float]] = []
    for row in rows:
        vec = _deserialize_embedding(row["embedding"])
        denom = (np.linalg.norm(query_vector) * np.linalg.norm(vec)) + 1e-9
        similarity = float(np.dot(query_vector, vec) / denom)
        scored.append((row_to_policy(row), similarity))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def retrieve_policies_for_event(
    embedding_provider: EmbeddingProvider, event_type: str, top_k: int = 5
) -> list[PersonalPolicy]:
    """event_type'a göre en alakalı politikaları getirir: embedding benzerliğiyle
    aday havuzu genişletilir, sonra event_type'ı TAM eşleşen politikalar
    global/diğer politikaların önüne alınır (bkz. §9 politika önceliği:
    daha spesifik kural genel kuraldan önce gelir)."""
    query_text = f"etkinlik türü: {event_type}"
    candidates = semantic_search_policies(embedding_provider, query_text, top_k=top_k * 2)

    def sort_key(item: tuple[PersonalPolicy, float]) -> tuple[bool, int, float]:
        policy, similarity = item
        exact_match = policy.structured_conditions.get("event_type") == event_type
        return (exact_match, policy.priority, similarity)

    ranked = sorted(candidates, key=sort_key, reverse=True)
    return [policy for policy, _ in ranked[:top_k]]
