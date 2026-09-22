"""JOB-01 (bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md): mail taramasının
kalıcı iş durumu — `scan_jobs` tablosunun okuma/yazma katmanı.

Önceden tarama tamamen senkron/bellek-içiydi: `app.state.scan_in_progress`
(bkz. src/ui/app.py, WRITE-01/scan_in_progress ile aynı desen) yalnızca
SÜREÇ hayatta olduğu sürece anlamlıydı — sunucu yeniden başlarsa iz bırakmaz,
kullanıcı ilerlemeyi göremez, sayfayı kapatınca tarama durumuna dair hiçbir
kayıt kalmazdı. Artık her tarama `scan_jobs`'ta bir satır: `src/services/
scan_inbox.py::run_scan_job` bu store'u kullanarak durumu ilerletir,
`src/ui/routes.py` taramayı arka plan thread'inde başlatıp HEMEN döner.

`candidates/store.py` ile AYNI stil: her fonksiyon kendi `get_connection()`'ını
açar (self-contained, web route'ları ve arka plan thread'i ayrı bağlantılar
kullanır)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.storage.db import get_connection

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_TIMED_OUT = "TIMED_OUT"

_ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

# Bir RUNNING işin bu süreden uzun sürmesi, sürecin KENDİSİ hayattayken
# (yeniden başlama değil) bir ağ çağrısının kalıcı olarak takıldığı anlamına
# gelir — gerçek bir thread'i güvenle zorla öldürmek Python'da mümkün
# olmadığından (bkz. modül docstring'i), bu yalnızca hesap başına kalıcı
# kilidi serbest bırakır (bir sonraki tarama yeni bir iş başlatabilir);
# takılı thread zararsız şekilde arka planda ölür (bkz. _resolve_if_running'in
# WHERE status='RUNNING' koruması, geç gelen bir güncellemenin çözülmüş bir
# işi geri açmasını engeller).
STALE_RUNNING_MINUTES = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job_dict(row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "status": row["status"],
        "total": row["total"],
        "processed": row["processed"],
        "candidates_found": row["candidates_found"],
        "skipped_errors": row["skipped_errors"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def create_scan_job(account_id: str) -> str:
    job_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO scan_jobs (id, account_id, status, created_at) VALUES (?, ?, ?, ?)",
            (job_id, account_id, STATUS_QUEUED, _now()),
        )
    return job_id


def mark_job_running(job_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE scan_jobs SET status = ?, started_at = ? WHERE id = ?",
            (STATUS_RUNNING, _now(), job_id),
        )


def update_job_progress(
    job_id: str, *, total: int, processed: int, candidates_found: int, skipped_errors: int
) -> None:
    """Bir tarama devam ederken (her mail işlendikten sonra, bkz.
    scan_inbox.py'nin on_checkpoint callback'i) çağrılır. `WHERE status =
    'RUNNING'` koruması: iş dışarıdan (reconcile_stale_running_jobs ile)
    zaten TIMED_OUT işaretlenmişse, takılı kalmış thread'in geç gelen bir
    ilerleme güncellemesi bunu SESSİZCE geri açmamalı."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE scan_jobs
            SET total = ?, processed = ?, candidates_found = ?, skipped_errors = ?
            WHERE id = ? AND status = ?
            """,
            (total, processed, candidates_found, skipped_errors, job_id, STATUS_RUNNING),
        )


def mark_job_succeeded(job_id: str, *, total: int, candidates_found: int, skipped_errors: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE scan_jobs
            SET status = ?, total = ?, processed = ?, candidates_found = ?, skipped_errors = ?, finished_at = ?
            WHERE id = ? AND status = ?
            """,
            (STATUS_SUCCEEDED, total, total, candidates_found, skipped_errors, _now(), job_id, STATUS_RUNNING),
        )


def mark_job_failed(job_id: str, error_message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE scan_jobs SET status = ?, error_message = ?, finished_at = ? WHERE id = ? AND status = ?",
            (STATUS_FAILED, error_message, _now(), job_id, STATUS_RUNNING),
        )


def get_job(job_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM scan_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job_dict(row) if row else None


def get_latest_job_for_account(account_id: str) -> dict | None:
    """Ekranda (Hesaplar) gösterilecek "son tarama" durumu — iş bitmiş olsa
    bile (SUCCEEDED/FAILED) en son satırı döner, yalnızca aktif işler değil."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM scan_jobs WHERE account_id = ? ORDER BY created_at DESC LIMIT 1",
            (account_id,),
        ).fetchone()
    return _row_to_job_dict(row) if row else None


def reconcile_stale_running_jobs() -> int:
    """`STALE_RUNNING_MINUTES`'ten uzun süredir RUNNING olan işleri TIMED_OUT
    yapar — bir sonraki tarama denemesinin hesap başına kalıcı kilidi
    sonsuza dek beklememesi için. `get_active_scan_job` her çağrıda bunu
    ÖNCE çalıştırır (bkz. altta). Döner: kaç iş etkilendi."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_RUNNING_MINUTES)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE scan_jobs SET status = ?, finished_at = ?, error_message = ? "
            "WHERE status = ? AND started_at IS NOT NULL AND started_at < ?",
            (STATUS_TIMED_OUT, _now(), "Tarama zaman aşımına uğradı (çok uzun sürdü).", STATUS_RUNNING, cutoff),
        )
    return cursor.rowcount


def reconcile_orphaned_jobs_at_startup() -> int:
    """Sunucu her başladığında BİR KEZ çağrılır (bkz. app.py::lifespan):
    önceki süreçten kalan QUEUED/RUNNING satırlar tanım gereği YETİM'dir
    (bu süreç onları hiç başlatmadı, kaldığı yerden devam eden bir thread
    yok) — yaşlarına bakılmaksızın (reconcile_stale_running_jobs'un aksine)
    hepsi hemen FAILED işaretlenir, hesap başına kalıcı kilit bir önceki
    çökme/yeniden başlatmadan sonra sonsuza dek takılı kalmaz."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE scan_jobs SET status = ?, finished_at = ?, error_message = ? WHERE status IN (?, ?)",
            (STATUS_FAILED, _now(), "Sunucu yeniden başlatıldı, tarama tamamlanmadı.", STATUS_QUEUED, STATUS_RUNNING),
        )
    return cursor.rowcount


def get_active_scan_job(account_id: str) -> dict | None:
    """Hesap başına kalıcı kilit: bir QUEUED/RUNNING iş varsa onu döner
    (çağıran yeni bir tarama BAŞLATMAMALI), yoksa None. Önce takılı kalmış
    RUNNING işleri temizler (bkz. reconcile_stale_running_jobs) ki eski bir
    takılma yeni bir taramayı sonsuza dek engellemesin."""
    reconcile_stale_running_jobs()
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in _ACTIVE_STATUSES)
        row = conn.execute(
            f"SELECT * FROM scan_jobs WHERE account_id = ? AND status IN ({placeholders}) "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, *_ACTIVE_STATUSES),
        ).fetchone()
    return _row_to_job_dict(row) if row else None
