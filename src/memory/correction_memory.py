"""Adaptive Correction Memory (bkz. docs/architecture-plan.md §10).

Kullanıcı bir öneriyi reddettiğinde düzeltmeyi yakalar (`user_corrections`),
"gelecekte de uygulayayım mı?" sorusunu sorar, yalnızca açık "evet" cevabıyla
src/policies/derivation.py üzerinden bir PersonalPolicy türetir. Onay olmadan
hiçbir kalıcı öğrenme olmaz (§10 "onay noktası", sessiz öğrenme yok)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.core.models import CandidateEvent, CorrectionScope, PolicySource, UserCorrection
from src.policies.derivation import derive_and_save_policy, save_derived_policy
from src.policies.store import deactivate_policy_by_id
from src.providers.base import EmbeddingProvider, LLMProvider
from src.providers.json_generation import JsonGenerationError
from src.rag.correction_retrieval import embed_and_store_classification_correction
from src.storage.db import get_connection


def candidate_snapshot(candidate: CandidateEvent) -> dict:
    return candidate.model_dump(
        mode="json",
        include={
            "event_type", "title", "start_datetime", "duration_minutes",
            "importance", "reminders", "location",
        },
    )


def save_user_correction(
    candidate: CandidateEvent,
    user_feedback_text: str,
    original_output: dict | None = None,
    corrected_output: dict | None = None,
    correction_type: str | None = None,
    user_id: str | None = None,
) -> UserCorrection:
    """``original_output``/``corrected_output`` verilmezse candidate'ın ŞU ANKİ
    hali kullanılır (reddetme akışı: candidate henüz düzenlenmemiştir, önce/
    sonra aynıdır). Düzenleme akışı (bkz. capture_edit_correction) çağrıldığı
    anda candidate ZATEN düzenlenmiş olduğu için ``original_output``'u
    düzenlemeden ÖNCEKİ anlık görüntüyle açıkça verir — aksi halde ikisi de
    aynı (düzenlenmiş) hale referans verirdi.

    ``correction_type``: None = alan düzeltmesi (reddetme/düzenleme, mevcut
    davranış); "classification" = mailin BAŞTAN takvimlik olmaması gerektiğine
    dair bir düzeltme (bkz. save_classification_correction) — retrieval bu
    ikisini karıştırmamak için filtreliyor (bkz. rag/correction_retrieval.py)."""
    current_snapshot = candidate_snapshot(candidate)
    correction = UserCorrection(
        correction_id=str(uuid.uuid4()),
        candidate_id=candidate.candidate_id,
        original_input=candidate.extraction_reason,
        original_output=original_output if original_output is not None else current_snapshot,
        user_feedback_text=user_feedback_text,
        corrected_output=corrected_output if corrected_output is not None else current_snapshot,
        correction_scope=CorrectionScope.SINGLE_EVENT,
        event_type=candidate.event_type,
        language="tr",
        approved_for_future_use=False,
        derived_policy_id=None,
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_corrections (
                correction_id, candidate_id, original_input, original_output,
                user_feedback_text, corrected_output, correction_scope, event_type,
                account_scope, sender_scope, language, approved_for_future_use,
                derived_policy_id, correction_type, created_at, user_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                correction.correction_id,
                correction.candidate_id,
                correction.original_input,
                json.dumps(correction.original_output),
                correction.user_feedback_text,
                json.dumps(correction.corrected_output),
                correction.correction_scope,
                correction.event_type,
                correction.account_scope,
                correction.sender_scope,
                correction.language,
                int(correction.approved_for_future_use),
                correction.derived_policy_id,
                correction_type,
                correction.created_at.isoformat(),
                user_id,
            ),
        )
    return correction


def save_classification_correction(
    embedding_provider: EmbeddingProvider,
    candidate: CandidateEvent,
    email_text: str,
    user_feedback_text: str,
    user_id: str | None = None,
) -> UserCorrection:
    """Kullanıcı bir mailin BAŞTAN hiç takvimlik olmaması gerektiğini
    belirttiğinde çağrılır (bkz. capture_correction_interactively) — bir
    alan (süre/önem/vb.) düzeltmesi değil, `is_calendar_worthy`
    sınıflandırmasının kendisine dair bir düzeltmedir. Mail metni embed edilip
    `correction_embeddings`'e yazılır; `retrieve_similar_classification_corrections`
    gelecekteki sınıflandırma çağrılarında bunu bağlam olarak kullanır."""
    correction = save_user_correction(candidate, user_feedback_text, correction_type="classification", user_id=user_id)
    embed_and_store_classification_correction(embedding_provider, correction.correction_id, email_text)
    return correction


_CORRECTIONS_QUERY = """
    SELECT uc.*, pp.natural_language_rule AS derived_rule_text, pp.active AS derived_rule_active
    FROM user_corrections uc
    LEFT JOIN personal_policies pp ON pp.policy_id = uc.derived_policy_id
"""


def _row_to_correction_dict(row) -> dict:
    return {
        "correction_id": row["correction_id"],
        "candidate_id": row["candidate_id"],
        "original_input": row["original_input"],
        "original_output": json.loads(row["original_output"]),
        "user_feedback_text": row["user_feedback_text"],
        "corrected_output": json.loads(row["corrected_output"]),
        "correction_scope": row["correction_scope"],
        "event_type": row["event_type"],
        "account_scope": row["account_scope"],
        "sender_scope": row["sender_scope"],
        "language": row["language"],
        "approved_for_future_use": bool(row["approved_for_future_use"]),
        "derived_policy_id": row["derived_policy_id"],
        "correction_type": row["correction_type"],
        "created_at": row["created_at"],
        "derived_rule_text": row["derived_rule_text"],
        "derived_rule_active": bool(row["derived_rule_active"]) if row["derived_rule_active"] is not None else None,
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
    }


def list_corrections(
    limit: int = 100, offset: int = 0, correction_type: str | None = None, user_id: str | None = None
) -> list[dict]:
    """Düzeltmelerim ekranı için — `user_corrections` bugüne kadar yazma-
    yalnızca bir tabloydu (tek okuyucu, correction_retrieval.py'nin
    semantik araması, o da yalnızca correction_type='classification'
    filtreli). LEFT JOIN personal_policies ile türetilen kuralın doğal dil
    metnini de getirir — ayrı bir sorgu gerekmesin diye.

    ``correction_type``: None = hepsi, "field" = correction_type IS NULL
    (alan düzeltmesi), "classification" = correction_type = 'classification'.
    ``user_id`` verilirse yalnızca o kullanıcının düzeltmeleri (bkz. plan
    "Per-user isolation")."""
    query = _CORRECTIONS_QUERY
    conditions = []
    params: list = []
    if correction_type == "classification":
        conditions.append("uc.correction_type = 'classification'")
    elif correction_type == "field":
        conditions.append("uc.correction_type IS NULL")
    if user_id is not None:
        conditions.append("uc.user_id = ?")
        params.append(user_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY uc.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_correction_dict(r) for r in rows]


def get_correction(correction_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(_CORRECTIONS_QUERY + " WHERE uc.correction_id = ?", (correction_id,)).fetchone()
    return _row_to_correction_dict(row) if row else None


def count_corrections(correction_type: str | None = None, user_id: str | None = None) -> int:
    query = "SELECT COUNT(*) FROM user_corrections"
    conditions = []
    params: list = []
    if correction_type == "classification":
        conditions.append("correction_type = 'classification'")
    elif correction_type == "field":
        conditions.append("correction_type IS NULL")
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()[0]


def set_correction_future_use(correction_id: str, approved: bool) -> None:
    """False'a çekildiğinde türetilmiş politika VARSA onu da pasifleştirir —
    aksi halde kullanıcı "kullanma" der ama kural uygulanmaya devam eder
    (görünürde kapatılmış ama fiilen hâlâ etkili bir kural kafa karıştırır)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT derived_policy_id FROM user_corrections WHERE correction_id = ?", (correction_id,)
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE user_corrections SET approved_for_future_use = ? WHERE correction_id = ?",
            (int(approved), correction_id),
        )
        derived_policy_id = row["derived_policy_id"]

    if not approved and derived_policy_id:
        deactivate_policy_by_id(derived_policy_id)


def delete_correction(correction_id: str) -> None:
    """`correction_embeddings` satırları AYNI transaction'da ÖNCE silinir
    (PRAGMA foreign_keys=ON, embeddings correction_id'ye referans veriyor).
    Türetilmiş politikaya DOKUNMAZ — bir düzeltme kaydını silmek, ondan
    türetilmiş ve hâlâ aktif olabilecek bir kuralı sessizce iptal etmemeli."""
    with get_connection() as conn:
        conn.execute("DELETE FROM correction_embeddings WHERE correction_id = ?", (correction_id,))
        conn.execute("DELETE FROM user_corrections WHERE correction_id = ?", (correction_id,))


def mark_correction_approved(
    correction_id: str,
    policy_id: str,
    correction_scope: CorrectionScope = CorrectionScope.EVENT_TYPE,
    sender_scope: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_corrections SET approved_for_future_use = 1, derived_policy_id = ?, "
            "correction_scope = ?, sender_scope = ? WHERE correction_id = ?",
            (policy_id, correction_scope, sender_scope, correction_id),
        )


def _choose_scope_interactively(
    candidate: CandidateEvent, sender: str | None
) -> tuple[str | None, str | None, CorrectionScope, str]:
    """Döner: (event_type_scope, sender_scope, correction_scope, açıklama).
    ``sender`` yalnızca mail kaynaklı candidate'lar için verilir (konuşma
    akışında None) — verilirse scope seçimine "bu göndericiden gelenler"
    seçeneği eklenir (bkz. §9 scope hiyerarşisi: sender, event_type'tan daha
    spesifiktir)."""
    if sender:
        choice = input(
            f"Yalnızca '{candidate.event_type}' türü etkinliklerde mi (1), "
            "yalnızca bu göndericiden gelen maillerde mi (2), yoksa her zaman mı (3)? [1/2/3] "
        ).strip()
    else:
        choice = input(
            f"Yalnızca '{candidate.event_type}' türü etkinliklerde mi (1), yoksa her zaman mı (2)? [1/2] "
        ).strip()

    if sender and choice == "2":
        return None, sender, CorrectionScope.SENDER, f"'{sender}' göndericisinden gelen mailler"
    if choice == ("3" if sender else "2"):
        # CorrectionScope'ta ayrı bir GLOBAL değeri yok; hesap genelinde
        # (her zaman) anlamına en yakın değer ACCOUNT.
        return None, None, CorrectionScope.ACCOUNT, "tüm etkinlikler"
    return candidate.event_type, None, CorrectionScope.EVENT_TYPE, f"'{candidate.event_type}' türü etkinlikler"


def capture_correction_interactively(
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    candidate: CandidateEvent,
    sender: str | None = None,
    email_text: str | None = None,
) -> None:
    """Kullanıcı bir öneriyi reddettikten hemen sonra çağrılır (bkz.
    review_and_confirm_candidate). Kullanıcı boş geçerse ya da "gelecekte de"
    demezse yalnızca ham düzeltme kaydedilir, hiçbir politika oluşmaz.

    ``email_text`` yalnızca mail kaynaklı candidate'lar için verilir (konuşma
    akışında None) — verilirse önce "bu mail hiç takvimlik değil miydi, yoksa
    bilgiler mi yanlıştı?" diye sorulur; ilki seçilirse bu bir alan düzeltmesi
    değil, is_calendar_worthy sınıflandırmasının kendisine dair bir düzeltme
    olarak ayrıca kaydedilir (bkz. save_classification_correction)."""
    if email_text:
        kind = input(
            "Bu mail hiç takvimlik değil miydi (1), yoksa bilgiler mi "
            "(tarih/süre/vb.) yanlıştı (2)? [1/2] "
        ).strip()
        if kind == "1":
            reason = input(
                "Neden takvimlik değildi? (isterseniz boş bırakıp Enter'a basabilirsiniz) "
            ).strip()
            if reason:
                save_classification_correction(embedding_provider, candidate, email_text, reason)
                print("Kaydettim: bu tür mailleri gelecekte daha iyi ayırt etmeye çalışacağım.")
            return

    feedback = input("Neden reddettiniz? (isterseniz boş bırakıp Enter'a basabilirsiniz) ").strip()
    if not feedback:
        return

    correction = save_user_correction(candidate, feedback)

    apply_future = input("Bunu gelecekte benzer etkinliklerde de uygulayayım mı? [e/h] ").strip().lower()
    if apply_future != "e":
        return

    event_type_scope, sender_scope, correction_scope, scope_desc = _choose_scope_interactively(candidate, sender)

    try:
        policy = derive_and_save_policy(
            llm,
            embedding_provider,
            feedback,
            event_type=event_type_scope,
            sender=sender_scope,
            infer_event_type=False,
            source=PolicySource.CORRECTION,
        )
    except JsonGenerationError:
        print("Bu düzeltmeyi işleyemedim, kural olarak kaydedemedim.")
        return

    if policy is None:
        print("Bu düzeltmeden somut bir kural çıkaramadım.")
        return

    mark_correction_approved(correction.correction_id, policy.policy_id, correction_scope, sender_scope)
    print(f"Kaydettim: {scope_desc} için gelecekte '{feedback}' uygulanacak.")


_STRUCTURED_ACTION_LABELS = {
    "default_duration_minutes": "süre",
    "reminder_minutes_before": "hatırlatıcı",
    "importance": "önem",
}


def capture_edit_correction(
    embedding_provider: EmbeddingProvider,
    candidate: CandidateEvent,
    original_snapshot: dict,
    edited_structured_action: dict,
    sender: str | None = None,
) -> None:
    """Kullanıcı önerideki bir alanı (süre/önem) doğrudan düzenleyip sonra
    ONAYLADIĞINDA çağrılır — reddetme değil ama yine de bir düzeltme sinyali
    (bkz. §7: kullanıcının bir öneriyi düzenlemesi de user_correction'dır).
    LLM'e ihtiyaç yok: hangi alanın ne olması gerektiği zaten kesin biliniyor
    (kullanıcı direkt yazdı) — save_derived_policy ile doğrudan kaydedilir
    (bkz. §11 deterministik karar ilkesi, LLM'e "tahmin ettirme")."""
    field_desc = ", ".join(
        f"{_STRUCTURED_ACTION_LABELS.get(k, k)} {v}" for k, v in edited_structured_action.items()
    )
    feedback = f"Kullanıcı önerideki alanı düzenledi: {field_desc}."
    correction = save_user_correction(candidate, feedback, original_output=original_snapshot)

    apply_future = input("Bu düzenlemeyi gelecekte benzer etkinliklerde de uygulayayım mı? [e/h] ").strip().lower()
    if apply_future != "e":
        return

    event_type_scope, sender_scope, correction_scope, scope_desc = _choose_scope_interactively(candidate, sender)

    policy = save_derived_policy(
        embedding_provider,
        feedback,
        edited_structured_action,
        event_type=event_type_scope,
        sender=sender_scope,
        source=PolicySource.CORRECTION,
    )
    mark_correction_approved(correction.correction_id, policy.policy_id, correction_scope, sender_scope)
    print(f"Kaydettim: {scope_desc} için gelecekte {field_desc} uygulanacak.")
