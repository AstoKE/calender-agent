"""RAG / Retrieval Layer — sınıflandırma düzeltmelerinin retrieval'ı
(bkz. docs/architecture-plan.md §9/§10).

`policy_retrieval.py`'den kasıtlı olarak AYRI: farklı bir amaca hizmet ediyor
("bu mail daha önce yanlış sınıflandırılmış mıydı?" vs "hangi kural
uygulanmalı?") ve ayrı bir tabloyu (`correction_embeddings`) sorguluyor —
bkz. §9 "Politika/düzeltme aynı index'te mi? Hayır" gerekçesi."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from src.providers.base import EmbeddingProvider
from src.storage.db import get_connection


def embed_and_store_classification_correction(
    embedding_provider: EmbeddingProvider, correction_id: str, email_text: str
) -> None:
    vector = embedding_provider.embed([email_text])[0]
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO correction_embeddings (correction_id, embedding, model_name, dim, created_at) "
            "VALUES (?,?,?,?,?)",
            (
                correction_id,
                np.array(vector, dtype=np.float32).tobytes(),
                embedding_provider.model_name,
                len(vector),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def retrieve_similar_classification_corrections(
    embedding_provider: EmbeddingProvider, email_text: str, top_k: int = 3, user_id: str | None = None
) -> list[str]:
    """Geçmişte kullanıcının "bu mail hiç takvimlik değildi" diye düzelttiği,
    mevcut mail metnine semantik olarak en benzer kayıtları, `is_calendar_worthy`
    promptuna doğrudan `context_chunks` olarak eklenebilecek hazır metinler
    halinde döner. Hiç kayıt yoksa boş liste döner (context_chunks=None ile
    aynı, davranış değişmez) — ve bu durumda `embedding_provider.embed()`
    HİÇ ÇAĞRILMAZ: her mail sınıflandırmasından önce boşuna bir native SDK
    çağrısı yapmak (canlı testte, henüz hiç düzeltme kaydedilmemişken bile
    art arda "Operation was cancelled" hatalarına denk gelindi) hem gereksiz
    hem gereksiz bir kırılganlık kaynağı; sorgu vektörü yalnızca gerçekten
    karşılaştırılacak bir kayıt varsa hesaplanır.
    """
    sql = """
        SELECT c.user_feedback_text, e.embedding
        FROM user_corrections c
        JOIN correction_embeddings e ON e.correction_id = c.correction_id
        WHERE c.correction_type = 'classification' AND e.model_name = ?
    """
    params: list = [embedding_provider.model_name]
    if user_id is not None:
        sql += " AND c.user_id = ?"
        params.append(user_id)

    with get_connection() as conn:
        # e.model_name filtresi: policy_retrieval.py'deki aynı gerekçe —
        # farklı bir embedding modeli/varyantı farklı bir vektör uzayı
        # üretir, karışık model kaynaklı yanlış benzerlik skorunu önler.
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return []

    query_vector = np.array(embedding_provider.embed([email_text])[0], dtype=np.float32)

    scored: list[tuple[str, float]] = []
    for row in rows:
        vec = np.frombuffer(row["embedding"], dtype=np.float32)
        denom = (np.linalg.norm(query_vector) * np.linalg.norm(vec)) + 1e-9
        similarity = float(np.dot(query_vector, vec) / denom)
        scored.append((row["user_feedback_text"], similarity))

    scored.sort(key=lambda item: item[1], reverse=True)
    return [
        "Daha önce buna benzer bir mail yanlışlıkla takvimlik sayılmıştı. "
        f'Kullanıcının düzeltmesi: "{feedback_text}"'
        for feedback_text, _ in scored[:top_k]
    ]
