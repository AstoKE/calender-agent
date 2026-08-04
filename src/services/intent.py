"""Niyet tespiti — Conversation Layer'ın ilk adımı (bkz. docs/architecture-plan.md §6/§20).

"Yarın takvimimde neler var" gibi sorgu niyetlerinin, hiç bir sınıflandırma
olmadan doğrudan etkinlik-oluşturma çıkarımına gönderilmesi (Hafta 1
prototipinin bilinen sınırı) yanlış/anlamsız sorulara yol açıyordu — bu
modül o boşluğu kapatır.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from src.core.models import IntentType
from src.providers.base import LLMProvider
from src.services.timeutil import DEFAULT_TIMEZONE, ensure_timezone


class IntentClassification(BaseModel):
    intent: IntentType
    query_range_start: datetime | None = None
    query_range_end: datetime | None = None


def _intent_system_prompt() -> str:
    today = datetime.now().astimezone()
    return (
        "Sen bir takvim asistanısın. Kullanıcının mesajının niyetini sınıflandır.\n"
        f"Bugünün tarihi ve saati: {today.isoformat()} (zaman dilimi: {DEFAULT_TIMEZONE}).\n"
        "Niyet türleri:\n"
        "- create_event: kullanıcı yeni bir etkinlik/randevu/toplantı EKLEMEK istiyor.\n"
        "- query_calendar: kullanıcı takviminde ne olduğunu SORUYOR, uygunluk/boşluk soruyor "
        "(örn. 'yarın takvimimde neler var', 'bu hafta ne kadar boşum').\n"
        "- update_event: kullanıcı VAR OLAN bir etkinliği değiştirmek/taşımak/iptal etmek istiyor.\n"
        "- other: yukarıdakilerin hiçbiri değil (sohbet, alakasız soru, vb.).\n"
        "query_calendar ise, sorulan tarih aralığını query_range_start/query_range_end olarak "
        "ISO 8601 hesapla. Göreceli ifadeleri (yarın, bu hafta, önümüzdeki cuma, vb.) yukarıdaki "
        "bugünün tarihine göre çöz — 'yarın' bugünün tarihi + 1 gün demektir. Gün belirtilmemişse "
        "bugünden başlayan makul bir aralık seç (örn. tüm gün).\n"
        "SADECE geçerli JSON döndür, başka hiçbir açıklama ekleme. Şema:\n"
        '{"intent": "create_event|query_calendar|update_event|other", '
        '"query_range_start": "YYYY-MM-DDTHH:MM:SS" veya null, '
        '"query_range_end": "YYYY-MM-DDTHH:MM:SS" veya null}'
    )


def classify_intent(llm: LLMProvider, user_text: str) -> IntentClassification:
    raw = llm.generate(_intent_system_prompt(), user_text, json_output=True)
    data = json.loads(raw)
    try:
        result = IntentClassification.model_validate(data)
    except Exception:
        return IntentClassification(intent=IntentType.OTHER)

    result.query_range_start = ensure_timezone(result.query_range_start)
    result.query_range_end = ensure_timezone(result.query_range_end)
    return result
