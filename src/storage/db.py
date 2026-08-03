"""SQLite bağlantı yönetimi ve şema migration'ı (bkz. docs/architecture-plan.md §12)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "calendar_agent.db"


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Şemayı (idempotent, CREATE TABLE IF NOT EXISTS) veritabanına uygular."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
