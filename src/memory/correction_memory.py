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
from src.providers.base import EmbeddingProvider, LLMProvider
from src.providers.json_generation import JsonGenerationError
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
) -> UserCorrection:
    """``original_output``/``corrected_output`` verilmezse candidate'ın ŞU ANKİ
    hali kullanılır (reddetme akışı: candidate henüz düzenlenmemiştir, önce/
    sonra aynıdır). Düzenleme akışı (bkz. capture_edit_correction) çağrıldığı
    anda candidate ZATEN düzenlenmiş olduğu için ``original_output``'u
    düzenlemeden ÖNCEKİ anlık görüntüyle açıkça verir — aksi halde ikisi de
    aynı (düzenlenmiş) hale referans verirdi."""
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
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_corrections (
                correction_id, candidate_id, original_input, original_output,
                user_feedback_text, corrected_output, correction_scope, event_type,
                account_scope, sender_scope, language, approved_for_future_use,
                derived_policy_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                correction.created_at.isoformat(),
            ),
        )
    return correction


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
) -> None:
    """Kullanıcı bir öneriyi reddettikten hemen sonra çağrılır (bkz.
    review_and_confirm_candidate). Kullanıcı boş geçerse ya da "gelecekte de"
    demezse yalnızca ham düzeltme kaydedilir, hiçbir politika oluşmaz."""
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
