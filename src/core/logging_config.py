"""Merkezi loglama yapılandırması.

DEBUG seviyesindeki ayrıntılar (LLM prompt/response, ayrıştırma sonuçları,
karar noktaları) ``data/debug.log`` dosyasına yazılır; konsolda yalnızca
WARNING+ görünür — normal kullanım kalabalıklaşmaz ama bir şey ters giderse
(örn. bir kullanıcı cevabı beklenmedik şekilde reddediliyor) log dosyasına
bakılıp tam olarak ne olduğu görülebilir.

``data/debug.log`` şifrelenmeden yazılıyor ve varsayılan olarak — plan
"Ürünleşme ve tasarım yol haritası" PRIV-01 bulgusu — mail gövdesi, sohbet
metni ve ham LLM girdi/çıktısı gibi hassas içerik burada UZUN metin olarak
görünebiliyordu. ``redact()`` bunu varsayılan olarak kısaltıp bir özet+hash'e
indiriyor (bkz. altta); çağıranlar (`json_generation.py`, `mail_analysis.py`)
loglanacak serbest metni bununla sarmalı."""

from __future__ import annotations

import hashlib
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "debug.log"

# Bir dosya ~10MB'a ulaşınca döner, en fazla 5 eski dosya tutulur (~60MB tavan) —
# canlı kullanımda debug.log sınırsız büyüyordu (gerçek kurulumda tek dosya
# 8MB+'a ulaşmıştı). Zaman bazlı bir saklama süresi (örn. "30 günden eskisini
# sil") bu tek-kullanıcılı yerel uygulama için ayrı bir cron/scheduler
# gerektirir — boyut bazlı rotasyon burada yeterli bir "saklama süresi" sınırı
# sağlıyor, bilinçli bir kapsam kararı.
_MAX_LOG_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("calendar_agent")
    root.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=_MAX_LOG_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"calendar_agent.{name}")


def _content_redaction_enabled() -> bool:
    # LOG_CONTENT=full: bilinçli bir yerel hata ayıklama kaçış kapısı (bkz.
    # .env.example) — sunucu/paylaşılan bir kurulumda ASLA set edilmemeli.
    return os.environ.get("LOG_CONTENT", "").strip().lower() != "full"


def redact(text: str, *, visible: int = 80) -> str:
    """Loglanacak serbest metni (mail gövdesi, sohbet metni, ham LLM
    girdi/çıktısı) varsayılan olarak maskeler — ``text[:visible]`` + kalan
    uzunluk + kararlı bir kısa hash döner, tam içeriği DEĞİL. Hash sayesinde
    "aynı istek iki kez mi gönderildi" gibi karşılaştırmalar hâlâ mümkün,
    ama gerçek içerik dosyada görünmüyor. ``visible`` sınırının altındaki
    kısa metinler (örn. bir mail konusu) genelde OLDUĞU GİBİ kalır — maskeleme
    asıl uzun/serbest metinler için anlamlı, kısa alanları anlamsızlaştırmaz.

    ``LOG_CONTENT=full`` ortam değişkeni bu maskelemeyi devre dışı bırakır
    (yerel, aktif hata ayıklama için — bkz. modül docstring'i)."""
    if not isinstance(text, str) or not _content_redaction_enabled() or len(text) <= visible:
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"{text[:visible]}…[{len(text) - visible} more chars redacted, sha256={digest}]"
