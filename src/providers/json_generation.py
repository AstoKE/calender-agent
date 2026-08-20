"""LLM'den JSON çıktısı isteyip parse eden ortak yardımcı.

Canlı testte (mail taraması) modelin bazen boş/geçersiz JSON döndürdüğü
görüldü. Üç kök neden tespit edildi:
1. Çıktı markdown kod bloğuna sarılmış (```json ... ```) — json.loads ilk
   karakterin backtick olmasından dolayı başarısız oluyor. Bu, reproducible
   bir formatlama alışkanlığı (aynı mail için tekrar tekrar oluyordu).
2. Bazen tamamen geçici/boş çıktı — bunun için retry yeterli.
3. Bazen `llm.generate()`'in kendisi bir istisna fırlatıyor (örn. Foundry
   Local native çağrısından "Operation was cancelled" — geçici bir SDK
   hatası, canlı testte görüldü). İlk sürümde retry yalnızca JSON parse
   hatalarını kapsıyordu, bu tür çağrı-seviyesi istisnalar hiç
   denenmeden çağırana sızıyordu; artık bunlar da retry kapsamında.

Bu yüzden burada hem kod bloğu temizleme hem retry (hem parse hem çağrı
hatalarında) tek bir yerde uygulanıp tüm çağıranlar (mail_analysis, intent,
extraction, policies) paylaşır.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from src.core.logging_config import get_logger
from src.providers.base import FileInputCapable, LLMProvider

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

logger = get_logger("json_generation")


class JsonGenerationError(Exception):
    """LLM, tekrar denemelere rağmen geçerli JSON üretemedi."""


def _strip_code_fence(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text).strip()


def _retry_and_parse(call: Callable[[], str], max_attempts: int) -> dict | list:
    """generate_json/generate_json_from_file'ın PAYLAŞTIĞI retry+temizleme
    döngüsü — ikisi arasındaki tek fark `llm.generate()`'i mi yoksa
    `llm.generate_from_file()`'ı mı çağırdıkları, o yüzden çağıran taraf
    bunu zero-arg bir closure olarak veriyor."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            raw = call()
        except Exception as e:
            logger.warning("attempt %d/%d generate call failed: %s", attempt, max_attempts, e)
            last_error = e
            continue
        logger.debug("attempt %d/%d raw output: %r", attempt, max_attempts, raw[:1000])
        try:
            parsed = json.loads(_strip_code_fence(raw))
            logger.debug("attempt %d/%d parsed OK: %s", attempt, max_attempts, parsed)
            return parsed
        except json.JSONDecodeError as e:
            logger.warning("attempt %d/%d JSON parse failed: %s | raw=%r", attempt, max_attempts, e, raw[:300])
            last_error = e
            continue
    logger.error("JSON generation exhausted %d attempts: %s", max_attempts, last_error)
    raise JsonGenerationError(f"{max_attempts} denemede geçerli JSON alınamadı: {last_error}")


def generate_json(
    llm: LLMProvider,
    system_prompt: str,
    user_prompt: str,
    context_chunks: list[str] | None = None,
    max_attempts: int = 2,
    allow_thinking: bool = False,
) -> dict:
    logger.debug("generate_json call | system=%r | user=%r", system_prompt[:300], user_prompt[:500])
    return _retry_and_parse(
        lambda: llm.generate(
            system_prompt,
            user_prompt,
            context_chunks=context_chunks,
            json_output=True,
            allow_thinking=allow_thinking,
        ),
        max_attempts,
    )


def generate_json_from_file(
    llm: FileInputCapable,
    system_prompt: str,
    user_prompt: str,
    file_bytes: bytes,
    mime_type: str,
    max_attempts: int = 2,
) -> dict | list:
    """`generate_json`'ın dosya (görsel/PDF) girişli karşılığı — bkz.
    FileInputCapable. Aynı retry+kod-bloğu-temizleme davranışı. Dönüş tipi
    `dict | list`: bu fonksiyonu kullanan tek çağıran (extract_candidate_events_from_file)
    modelden HER ZAMAN bir JSON listesi ister (bir dosyada birden fazla ayrı
    etkinlik olabileceğinden), ama model buna uymayıp çıplak bir nesne
    döndürebilir — çağıran taraf ikisini de ele alır."""
    logger.debug(
        "generate_json_from_file call | system=%r | user=%r | mime_type=%s | %d bytes",
        system_prompt[:300], user_prompt[:500], mime_type, len(file_bytes),
    )
    return _retry_and_parse(
        lambda: llm.generate_from_file(system_prompt, user_prompt, file_bytes, mime_type, json_output=True),
        max_attempts,
    )
