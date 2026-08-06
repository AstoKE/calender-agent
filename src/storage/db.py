"""SQLite bağlantı yönetimi ve şema migration'ı (bkz. docs/architecture-plan.md §12)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "calendar_agent.db"

# CREATE TABLE IF NOT EXISTS, halihazırda var olan bir tabloya sonradan
# eklenen sütunları geriye dönük eklemez (canlı testte email_messages.labels
# eksik kaldığı için deterministik filtre hiç devreye giremedi). Tam bir
# migration framework'üne geçmeden önce, MVP için küçük elle yönetilen bir
# sütun-ekleme listesi yeterli (bkz. docs/architecture-plan.md §12).
_ADHOC_COLUMN_MIGRATIONS = [
    ("email_messages", "labels", "TEXT"),
]


def _apply_adhoc_migrations(conn: sqlite3.Connection) -> None:
    for table, column, col_type in _ADHOC_COLUMN_MIGRATIONS:
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Şemayı (idempotent, CREATE TABLE IF NOT EXISTS) veritabanına uygular."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        _apply_adhoc_migrations(conn)
        conn.commit()


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
