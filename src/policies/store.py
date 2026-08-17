"""Policy Store (bkz. docs/architecture-plan.md §9/§10/§12).

Aktif kişisel kuralların CRUD işlemleri. Embedding üretimi ve retrieval
mantığı burada değil, src/rag/policy_retrieval.py'de — ayrı sorumluluk
(Policy Store veriyi tutar, RAG Layer onu arar/getirir)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.core.models import PersonalPolicy, PolicyScope, PolicySource
from src.storage.db import get_connection


def add_policy(
    category: str,
    natural_language_rule: str,
    structured_action: dict,
    event_type: str | None = None,
    language: str = "tr",
    priority: int = 0,
    source: PolicySource = PolicySource.MANUAL,
) -> PersonalPolicy:
    scope = PolicyScope.EVENT_TYPE if event_type else PolicyScope.GLOBAL
    now = datetime.now(timezone.utc)
    policy = PersonalPolicy(
        policy_id=str(uuid.uuid4()),
        category=category,
        scope=scope,
        natural_language_rule=natural_language_rule,
        language=language,
        structured_conditions={"event_type": event_type} if event_type else {},
        structured_action=structured_action,
        priority=priority,
        version=1,
        active=True,
        approved_by_user=True,
        source=source,
        created_at=now,
        updated_at=now,
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO personal_policies (
                policy_id, category, scope, natural_language_rule, language,
                structured_conditions, structured_action, priority, version,
                active, approved_by_user, source, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                policy.policy_id,
                policy.category,
                policy.scope,
                policy.natural_language_rule,
                policy.language,
                json.dumps(policy.structured_conditions),
                json.dumps(policy.structured_action),
                policy.priority,
                policy.version,
                int(policy.active),
                int(policy.approved_by_user),
                policy.source,
                now.isoformat(),
                now.isoformat(),
            ),
        )
    return policy


def row_to_policy(row) -> PersonalPolicy:
    return PersonalPolicy(
        policy_id=row["policy_id"],
        category=row["category"],
        scope=row["scope"],
        natural_language_rule=row["natural_language_rule"],
        language=row["language"],
        structured_conditions=json.loads(row["structured_conditions"] or "{}"),
        structured_action=json.loads(row["structured_action"] or "{}"),
        priority=row["priority"],
        version=row["version"],
        active=bool(row["active"]),
        approved_by_user=bool(row["approved_by_user"]),
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_active_policies() -> list[PersonalPolicy]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM personal_policies WHERE active = 1").fetchall()
    return [row_to_policy(r) for r in rows]


def find_active_conflicting_policy(category: str, event_type: str | None) -> PersonalPolicy | None:
    """Aynı `category` (structured_action anahtarı) ve aynı `event_type` kapsamında
    (ikisi de global ise de eşleşir) aktif bir politika var mı arar (bkz. §9
    çelişki tespiti). Bulunursa çağıran bunu `deactivate_policy` ile versiyonlayıp
    yenisiyle değiştirmeli — aynı kural iki kez tanımlanınca iki ayrı aktif
    politika birikmesin diye."""
    for policy in get_active_policies():
        if category not in policy.structured_action:
            continue
        if policy.structured_conditions.get("event_type") == event_type:
            return policy
    return None


def deactivate_policy(policy: PersonalPolicy) -> None:
    """Bir politikayı pasifleştirir (active=0) ve eski halini `policy_versions`'a
    snapshot olarak yazar — silinmez, denetim/geri alma için saklanır (§9).

    NOT: `policy_versions.superseded_by` şemada `policy_versions(version_id)`'ye
    referans veriyor (yeni bir `personal_policies.policy_id`'ye değil) — burada
    onu doldurmaya çalışmıyoruz (yeni aktif politika için ayrıca bir version_id
    üretmiyoruz), NULL bırakılıyor. Eski versiyon yine de silinmeden saklanıyor,
    sadece "hangi policy_id'nin yerini aldı" linki yok — MVP için yeterli."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE personal_policies SET active = 0, updated_at = ? WHERE policy_id = ?",
            (now, policy.policy_id),
        )
        conn.execute(
            """
            INSERT INTO policy_versions (version_id, policy_id, version, snapshot, created_at, superseded_by)
            VALUES (?,?,?,?,?,NULL)
            """,
            (str(uuid.uuid4()), policy.policy_id, policy.version, policy.model_dump_json(), now),
        )
