"""Policy Store (bkz. docs/architecture-plan.md §9/§10/§12).

Aktif kişisel kuralların CRUD işlemleri. Embedding üretimi ve retrieval
mantığı burada değil, src/rag/policy_retrieval.py'de — ayrı sorumluluk
(Policy Store veriyi tutar, RAG Layer onu arar/getirir)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr

from src.core.models import PersonalPolicy, PolicyScope, PolicySource
from src.storage.db import get_connection


def normalize_sender(raw: str) -> str:
    """'"İsim" <adres@ornek.com>' gibi ham bir From başlığından karşılaştırma
    için kararlı bir anahtar üretir — aynı gönderici farklı maillerde farklı
    görünen isimle gelebilir, bu yüzden yalnızca adres kısmı (küçük harfe
    çevrilmiş) kullanılır. Zaten sade bir adres verilirse (görünen isim yok)
    değişmeden döner."""
    return (parseaddr(raw)[1] or raw).strip().lower()


def _structured_conditions(event_type: str | None, sender: str | None) -> dict:
    if sender:
        return {"sender": normalize_sender(sender)}
    if event_type:
        return {"event_type": event_type}
    return {}


def _policy_scope(event_type: str | None, sender: str | None) -> PolicyScope:
    if sender:
        return PolicyScope.SENDER
    if event_type:
        return PolicyScope.EVENT_TYPE
    return PolicyScope.GLOBAL


def add_policy(
    category: str,
    natural_language_rule: str,
    structured_action: dict,
    event_type: str | None = None,
    sender: str | None = None,
    language: str = "tr",
    priority: int = 0,
    source: PolicySource = PolicySource.MANUAL,
) -> PersonalPolicy:
    scope = _policy_scope(event_type, sender)
    now = datetime.now(timezone.utc)
    policy = PersonalPolicy(
        policy_id=str(uuid.uuid4()),
        category=category,
        scope=scope,
        natural_language_rule=natural_language_rule,
        language=language,
        structured_conditions=_structured_conditions(event_type, sender),
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


def list_policies(include_inactive: bool = False) -> list[PersonalPolicy]:
    """Kurallarım ekranı için — `get_active_policies` yalnızca aktifleri
    döndüğünden (RAG/Rule Engine çağırıyor, adı bilerek korunuyor, aşağıya
    bkz.) pasif kuralları görecek yeni bir okuma yolu gerekiyordu."""
    query = "SELECT * FROM personal_policies"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY active DESC, updated_at DESC"
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
    return [row_to_policy(r) for r in rows]


def get_active_policies() -> list[PersonalPolicy]:
    """İSİM KORUNDU — src/rag/policy_retrieval.py ve Rule Engine yolunda
    çağrıcıları var, bir UI dilimi için bunları değiştirmek kapsam dışı."""
    return list_policies(include_inactive=False)


def get_policy(policy_id: str) -> PersonalPolicy | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM personal_policies WHERE policy_id = ?", (policy_id,)).fetchone()
    return row_to_policy(row) if row else None


def count_active_policies() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM personal_policies WHERE active = 1").fetchone()[0]


def find_active_conflicting_policy(
    category: str, event_type: str | None = None, sender: str | None = None
) -> PersonalPolicy | None:
    """Aynı `category` (structured_action anahtarı) ve aynı kapsamda (event_type/
    sender/global — ikisi de global ise de eşleşir) aktif bir politika var mı
    arar (bkz. §9 çelişki tespiti). Bulunursa çağıran bunu `deactivate_policy`
    ile versiyonlayıp yenisiyle değiştirmeli — aynı kural iki kez tanımlanınca
    iki ayrı aktif politika birikmesin diye."""
    target = _structured_conditions(event_type, sender)
    for policy in get_active_policies():
        if category not in policy.structured_action:
            continue
        if policy.structured_conditions == target:
            return policy
    return None


def get_active_policies_for_sender(sender: str) -> list[PersonalPolicy]:
    """Belirli bir gönderene özel (sender-scope'lu) aktif politikaları döner
    (bkz. §9). Semantik retrieval'a değil doğrudan eşleşmeye dayanır — gönderen
    adresi biliniyorsa bu deterministik bir eşleşmedir, tahmine gerek yok."""
    normalized = normalize_sender(sender)
    return [p for p in get_active_policies() if p.structured_conditions.get("sender") == normalized]


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


def deactivate_policy_by_id(policy_id: str) -> bool:
    """Web route'ları yalnızca id'ye sahip — `deactivate_policy` OBJE alıyor
    (.version + .model_dump_json() için). Bulunamazsa/zaten pasifse False
    döner, çağıran ayrıca hata göstermek zorunda kalmaz."""
    policy = get_policy(policy_id)
    if policy is None or not policy.active:
        return False
    deactivate_policy(policy)
    return True


def reactivate_policy(policy_id: str) -> PersonalPolicy | None:
    """Bir pasif politikayı yeniden aktifleştirir (version+1, policy_versions'a
    yeni snapshot). Aynı category+kapsamda BAŞKA bir aktif politika varsa
    None döner — find_active_conflicting_policy'nin engellemek için
    yazıldığı "iki aktif politika" durumunu burada sessizce yaratmaz."""
    policy = get_policy(policy_id)
    if policy is None or policy.active:
        return None

    for category in policy.structured_action:
        conflict = find_active_conflicting_policy(
            category,
            event_type=policy.structured_conditions.get("event_type"),
            sender=policy.structured_conditions.get("sender"),
        )
        if conflict is not None:
            return None

    now = datetime.now(timezone.utc).isoformat()
    new_version = policy.version + 1
    with get_connection() as conn:
        conn.execute(
            "UPDATE personal_policies SET active = 1, version = ?, updated_at = ? WHERE policy_id = ?",
            (new_version, now, policy_id),
        )
        conn.execute(
            "INSERT INTO policy_versions (version_id, policy_id, version, snapshot, created_at, superseded_by) "
            "VALUES (?,?,?,?,?,NULL)",
            (str(uuid.uuid4()), policy_id, new_version, policy.model_dump_json(), now),
        )
    return get_policy(policy_id)


def list_policy_versions(policy_id: str) -> list[dict]:
    """`policy_versions` bugüne kadar yazma-yalnızca bir tablo (superseded_by
    da hiçbir zaman doldurulmuyor, bkz. deactivate_policy'nin notu — versiyon
    geçmişi görünümü bu yüzden seyrek görünebilir, burada 'düzeltilmiyor')."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT version_id, version, snapshot, created_at FROM policy_versions "
            "WHERE policy_id = ? ORDER BY version DESC",
            (policy_id,),
        ).fetchall()
    return [
        {
            "version_id": r["version_id"],
            "version": r["version"],
            "snapshot": json.loads(r["snapshot"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
