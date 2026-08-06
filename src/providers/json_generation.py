"""LLM'den JSON çıktısı isteyip parse eden ortak yardımcı.

Canlı testte (mail taraması) modelin bazen boş/geçersiz JSON döndürdüğü
görüldü. İki kök neden tespit edildi:
1. Çıktı markdown kod bloğuna sarılmış (```json ... ```) — json.loads ilk
   karakterin backtick olmasından dolayı başarısız oluyor. Bu, reproducible
   bir formatlama alışkanlığı (aynı mail için tekrar tekrar oluyordu).
2. Bazen tamamen geçici/boş çıktı (bkz. "Operation was cancelled" hatasının
   da geçici olduğu bulgusu) — bunun için retry yeterli.

Bu yüzden burada hem kod bloğu temizleme hem retry tek bir yerde uygulanıp
tüm çağıranlar (mail_analysis, intent, extraction, policies) paylaşır.
"""

from __future__ import annotations

import json
import re

from src.providers.base import LLMProvider

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class JsonGenerationError(Exception):
    """LLM, tekrar denemelere rağmen geçerli JSON üretemedi."""


def _strip_code_fence(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text).strip()


def generate_json(
    llm: LLMProvider,
    system_prompt: str,
    user_prompt: str,
    context_chunks: list[str] | None = None,
    max_attempts: int = 2,
) -> dict:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        raw = llm.generate(system_prompt, user_prompt, context_chunks=context_chunks, json_output=True)
        try:
            return json.loads(_strip_code_fence(raw))
        except json.JSONDecodeError as e:
            last_error = e
            continue
    raise JsonGenerationError(f"{max_attempts} denemede geçerli JSON alınamadı: {last_error}")
