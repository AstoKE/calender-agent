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
from src.policies.derivation import derive_and_save_policy
from src.providers.base import EmbeddingProvider, LLMProvider
from src.providers.json_generation import JsonGenerationError
from src.storage.db import get_connection


def _candidate_snapshot(candidate: CandidateEvent) -> dict:
    return candidate.model_dump(
        mode="json",
        include={
            "event_type", "title", "start_datetime", "duration_minutes",
            "importance", "reminders", "location",
        },
    )


def save_user_correction(candidate: CandidateEvent, user_feedback_text: str) -> UserCorrection:
    # MVP: yapılandırılmış bir "düzenle" arayüzü yok, tek kaynak candidate'ın
    # reddedildiği andaki hali — original_output ve corrected_output bu yüzden
    # aynı anlık görüntü. correction_scope her zaman SINGLE_EVENT: asıl scope
    # kararı (event_type/global) yalnızca kullanıcı "gelecekte de" derse,
    # derive_and_save_policy çağrısında verilir.
    snapshot = _candidate_snapshot(candidate)
    correction = UserCorrection(
        correction_id=str(uuid.uuid4()),
        candidate_id=candidate.candidate_id,
        original_input=candidate.extraction_reason,
        original_output=snapshot,
        user_feedback_text=user_feedback_text,
        corrected_output=snapshot,
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


def capture_correction_interactively(
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    candidate: CandidateEvent,
    sender: str | None = None,
) -> None:
    """Kullanıcı bir öneriyi reddettikten hemen sonra çağrılır (bkz.
    review_and_confirm_candidate). Kullanıcı boş geçerse ya da "gelecekte de"
    demezse yalnızca ham düzeltme kaydedilir, hiçbir politika oluşmaz.

    ``sender`` yalnızca mail kaynaklı candidate'lar için verilir (konuşma
    akışında None) — verilirse scope seçimine "bu göndericiden gelenler"
    seçeneği eklenir (bkz. §9 scope hiyerarşisi: sender, event_type'tan daha
    spesifiktir)."""
    feedback = input("Neden reddettiniz? (isterseniz boş bırakıp Enter'a basabilirsiniz) ").strip()
    if not feedback:
        return

    correction = save_user_correction(candidate, feedback)

    apply_future = input("Bunu gelecekte benzer etkinliklerde de uygulayayım mı? [e/h] ").strip().lower()
    if apply_future != "e":
        return

    if sender:
        scope_choice = input(
            f"Yalnızca '{candidate.event_type}' türü etkinliklerde mi (1), "
            "yalnızca bu göndericiden gelen maillerde mi (2), yoksa her zaman mı (3)? [1/2/3] "
        ).strip()
    else:
        scope_choice = input(
            f"Yalnızca '{candidate.event_type}' türü etkinliklerde mi (1), yoksa her zaman mı (2)? [1/2] "
        ).strip()

    if sender and scope_choice == "2":
        event_type_scope, sender_scope = None, sender
        correction_scope = CorrectionScope.SENDER
        scope_desc = f"'{sender}' göndericisinden gelen mailler"
    elif scope_choice == ("3" if sender else "2"):
        event_type_scope, sender_scope = None, None
        # CorrectionScope'ta ayrı bir GLOBAL değeri yok; hesap genelinde
        # (her zaman) anlamına en yakın değer ACCOUNT.
        correction_scope = CorrectionScope.ACCOUNT
        scope_desc = "tüm etkinlikler"
    else:
        event_type_scope, sender_scope = candidate.event_type, None
        correction_scope = CorrectionScope.EVENT_TYPE
        scope_desc = f"'{candidate.event_type}' türü etkinlikler"

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
