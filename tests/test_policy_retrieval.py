"""src/rag/policy_retrieval.py için testler — özellikle event_type-scope
sızıntısı regresyonu (bkz. canlı testte bulunan bug: bir event_type'a
scope'lanmış bir politika, semantik benzerlik yüzünden BAŞKA event_type'lı
candidate'lara da uygulanabiliyordu)."""

from __future__ import annotations

from src.policies.store import add_policy
from src.providers.base import EmbeddingProvider
from src.rag.policy_retrieval import embed_and_store_policy, retrieve_policies_for_event


class _FakeEmbeddingProvider(EmbeddingProvider):
    """Her metni kendi hash'inden türetilmiş sabit bir vektöre eşler — aynı
    metin her zaman aynı vektörü üretir, gerçek bir semantik model gerekmez.
    Testler yalnızca "her politika en azından makul bir benzerlikle
    bulunabiliyor mu" ve "filtreleme doğru mu" sorularını kontrol ediyor,
    gerçek semantik yakınlığı değil."""

    def embed(self, texts):
        return [[1.0, 0.5, float(len(t) % 7)] for t in texts]

    @property
    def model_name(self):
        return "fake-embedding"

    @property
    def dimension(self):
        return 3


def _add_and_embed(embedding_provider, **kwargs):
    policy = add_policy(**kwargs)
    embed_and_store_policy(embedding_provider, policy)
    return policy


def test_event_type_scoped_policy_does_not_leak_to_other_event_type(temp_db):
    # Regresyon: "sınav hatırlatıcıları 3 gün önce" (event_type=exam) bir
    # toplantı (meeting) candidate'ına ASLA uygulanmamalı.
    embedding_provider = _FakeEmbeddingProvider()
    _add_and_embed(
        embedding_provider,
        category="reminder",
        natural_language_rule="Sınav tarihlerinin anımsatıcıları son 3 gün öncesinden olsun",
        structured_action={"reminder_minutes_before": 4320},
        event_type="exam",
    )

    results = retrieve_policies_for_event(embedding_provider, event_type="meeting")
    assert results == []


def test_event_type_scoped_policy_applies_to_matching_event_type(temp_db):
    embedding_provider = _FakeEmbeddingProvider()
    policy = _add_and_embed(
        embedding_provider,
        category="reminder",
        natural_language_rule="Sınav tarihlerinin anımsatıcıları son 3 gün öncesinden olsun",
        structured_action={"reminder_minutes_before": 4320},
        event_type="exam",
    )

    results = retrieve_policies_for_event(embedding_provider, event_type="exam")
    assert [p.policy_id for p in results] == [policy.policy_id]


def test_global_policy_applies_to_any_event_type(temp_db):
    embedding_provider = _FakeEmbeddingProvider()
    policy = _add_and_embed(
        embedding_provider,
        category="duration",
        natural_language_rule="Etkinlikler için varsayılan süre 45 dakika olsun",
        structured_action={"default_duration_minutes": 45},
        event_type=None,
    )

    results = retrieve_policies_for_event(embedding_provider, event_type="travel")
    assert [p.policy_id for p in results] == [policy.policy_id]


def test_matching_event_type_ranked_above_global(temp_db):
    embedding_provider = _FakeEmbeddingProvider()
    global_policy = _add_and_embed(
        embedding_provider,
        category="duration",
        natural_language_rule="Etkinlikler için varsayılan süre 45 dakika olsun",
        structured_action={"default_duration_minutes": 45},
        event_type=None,
    )
    meeting_policy = _add_and_embed(
        embedding_provider,
        category="duration",
        natural_language_rule="Toplantılar için varsayılan süre 30 dakika olsun",
        structured_action={"default_duration_minutes": 30},
        event_type="meeting",
    )

    results = retrieve_policies_for_event(embedding_provider, event_type="meeting")
    result_ids = [p.policy_id for p in results]
    assert meeting_policy.policy_id in result_ids
    assert global_policy.policy_id in result_ids
    assert result_ids.index(meeting_policy.policy_id) < result_ids.index(global_policy.policy_id)
