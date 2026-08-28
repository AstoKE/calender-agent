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
    ("user_corrections", "correction_type", "TEXT"),
    ("candidate_events", "google_event_id", "TEXT"),
    ("candidate_events", "previous_snapshot", "TEXT"),
    ("calendar_events_cache", "raw_json", "TEXT"),
    ("accounts", "user_id", "TEXT"),
    ("user_preferences", "user_id", "TEXT"),
    ("personal_policies", "user_id", "TEXT"),
    ("user_corrections", "user_id", "TEXT"),
]


def _apply_adhoc_migrations(conn: sqlite3.Connection) -> None:
    for table, column, col_type in _ADHOC_COLUMN_MIGRATIONS:
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db(db_path: Path | None = None) -> None:
    """Şemayı (idempotent, CREATE TABLE IF NOT EXISTS) veritabanına uygular.

    ``db_path=None`` ise ``DEFAULT_DB_PATH`` kullanılır — bu, sabit bir varsayılan
    PARAMETRE değeri olarak DEĞİL, gövde içinde her çağrıda modül global'i
    olarak okunuyor. Aksi halde (Python'ın "varsayılan parametre tanım anında
    bir kere bağlanır" davranışı yüzünden) testlerde ``DEFAULT_DB_PATH``'i
    monkeypatch'lemek, zaten import edilmiş bu fonksiyonun varsayılanını
    değiştirmezdi — testler farkında olmadan gerçek DB'ye yazardı."""
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        # WAL: dosya başlığında kalıcı bir ayar, bir kez burada set edilmesi
        # yeterli — sonraki TÜM bağlantılar (get_connection dahil) otomatik
        # WAL modunda açılır. Web sunucusunun eşzamanlı istekleri (her biri
        # kendi kısa sqlite3.connect'ini açıyor, bkz. get_connection) rollback-
        # journal modunda birbirini kilitleyebiliyordu (canlı testte
        # "database is locked" ile bulundu); WAL'da okuyucular yazıcıyı
        # bloklamıyor, yalnızca yazıcı-yazıcı çakışması kalıyor.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(schema_sql)
        _apply_adhoc_migrations(conn)
        conn.commit()


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    # timeout: Python'ın varsayılanı (5sn) kısa kilit çakışmalarında bile
    # anında "database is locked" fırlatabiliyordu — 15sn, geriye kalan
    # (artık çok kısa olması gereken, bkz. chat_flow.py'nin get_connection
    # kullanım deseni) yazma çakışmalarına beklemek için pay tanıyor.
    conn = sqlite3.connect(db_path, timeout=15.0)
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
