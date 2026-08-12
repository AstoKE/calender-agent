"""Merkezi loglama yapılandırması.

DEBUG seviyesindeki ayrıntılar (LLM prompt/response, ayrıştırma sonuçları,
karar noktaları) ``data/debug.log`` dosyasına yazılır; konsolda yalnızca
WARNING+ görünür — normal kullanım kalabalıklaşmaz ama bir şey ters giderse
(örn. bir kullanıcı cevabı beklenmedik şekilde reddediliyor) log dosyasına
bakılıp tam olarak ne olduğu görülebilir.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "debug.log"

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("calendar_agent")
    root.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"calendar_agent.{name}")
