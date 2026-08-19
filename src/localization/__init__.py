"""i18n çekirdeği: anahtar tabanlı çeviri arama + dil normalizasyonu.

Kural: translate() ASLA KeyError/İstisna fırlatmaz. Bir yazım hatası ya da
henüz çevrilmemiş bir anahtar, boş bir sayfa ya da 500 yerine görünür ama
zararsız bir anahtar string'i basar — bkz. MESSAGES.get zincirindeki üç
seviyeli geri düşüş."""

from __future__ import annotations

from typing import Callable

from src.localization.catalog import DEFAULT_LANGUAGE, MESSAGES, SUPPORTED_LANGUAGES

__all__ = [
    "SUPPORTED_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "normalize_language",
    "translate",
    "translator_for",
    "missing_keys",
]


def normalize_language(raw: str | None) -> str:
    """'EN', 'en-GB', None, '' -> desteklenen bir dil kodu. Asla hata atmaz."""
    if not raw:
        return DEFAULT_LANGUAGE
    code = raw.strip().lower().split("-")[0].split("_")[0]
    return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def translate(key: str, lang: str, **params) -> str:
    """MESSAGES[key][lang] -> MESSAGES[key][DEFAULT_LANGUAGE] -> key.
    params verilirse str.format ile uygulanır; format hatası da yutulur
    (eksik bir parametre bir düzenleme sırasında 500'e dönüşmesin)."""
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text


def translator_for(lang: str) -> Callable[..., str]:
    """Şablonlara geçirilecek t(key, **params) closure'ı."""
    lang = normalize_language(lang)

    def _t(key: str, **params) -> str:
        return translate(key, lang, **params)

    return _t


def missing_keys(lang: str) -> list[str]:
    """Test yardımcısı: verilen dilde çevirisi eksik anahtarları döner."""
    return [key for key, entry in MESSAGES.items() if lang not in entry]
