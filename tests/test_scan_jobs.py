"""JOB-01 (bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md): scan_jobs
tablosunun okuma/yazma katmanı — gerçek Gmail/LLM'e hiç dokunmuyor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.connectors.account_registry import ensure_account_registered
from src.services import scan_jobs
from src.storage.db import get_connection


def _account(account_id: str = "acc1") -> str:
    ensure_account_registered(account_id, provider="google", email=f"{account_id}@example.com")
    return account_id


def test_create_scan_job_starts_as_queued():
    account_id = _account()
    job_id = scan_jobs.create_scan_job(account_id)
    job = scan_jobs.get_job(job_id)
    assert job["status"] == scan_jobs.STATUS_QUEUED
    assert job["total"] == 0
    assert job["started_at"] is None


def test_mark_job_running_sets_started_at():
    job_id = scan_jobs.create_scan_job(_account())
    scan_jobs.mark_job_running(job_id)
    job = scan_jobs.get_job(job_id)
    assert job["status"] == scan_jobs.STATUS_RUNNING
    assert job["started_at"] is not None


def test_update_job_progress_only_applies_while_running():
    job_id = scan_jobs.create_scan_job(_account())
    # Henüz RUNNING değil (hâlâ QUEUED) — ilerleme güncellemesi sessizce
    # hiçbir şey yapmamalı (WHERE status='RUNNING' koruması).
    scan_jobs.update_job_progress(job_id, total=10, processed=3, candidates_found=1, skipped_errors=0)
    assert scan_jobs.get_job(job_id)["processed"] == 0

    scan_jobs.mark_job_running(job_id)
    scan_jobs.update_job_progress(job_id, total=10, processed=3, candidates_found=1, skipped_errors=0)
    job = scan_jobs.get_job(job_id)
    assert job["total"] == 10
    assert job["processed"] == 3
    assert job["candidates_found"] == 1


def test_mark_job_succeeded_sets_final_counts_and_finished_at():
    job_id = scan_jobs.create_scan_job(_account())
    scan_jobs.mark_job_running(job_id)
    scan_jobs.mark_job_succeeded(job_id, total=5, candidates_found=2, skipped_errors=1)
    job = scan_jobs.get_job(job_id)
    assert job["status"] == scan_jobs.STATUS_SUCCEEDED
    assert job["processed"] == 5  # total ile eşit — bitince "hepsi işlendi"
    assert job["candidates_found"] == 2
    assert job["skipped_errors"] == 1
    assert job["finished_at"] is not None


def test_mark_job_failed_records_error_message():
    job_id = scan_jobs.create_scan_job(_account())
    scan_jobs.mark_job_running(job_id)
    scan_jobs.mark_job_failed(job_id, "boom")
    job = scan_jobs.get_job(job_id)
    assert job["status"] == scan_jobs.STATUS_FAILED
    assert job["error_message"] == "boom"


def test_a_resolved_job_ignores_late_progress_and_completion_updates():
    # Bir iş dışarıdan (reconcile_stale_running_jobs) zaten TIMED_OUT
    # işaretlendikten SONRA, takılı kalmış eski thread'in geç gelen
    # güncellemeleri SESSİZCE hiçbir şey yapmamalı — WHERE status='RUNNING'
    # koruması, bkz. modül docstring'i.
    job_id = scan_jobs.create_scan_job(_account())
    scan_jobs.mark_job_running(job_id)
    with get_connection() as conn:
        conn.execute("UPDATE scan_jobs SET status = ? WHERE id = ?", (scan_jobs.STATUS_TIMED_OUT, job_id))

    scan_jobs.update_job_progress(job_id, total=99, processed=99, candidates_found=99, skipped_errors=99)
    scan_jobs.mark_job_succeeded(job_id, total=99, candidates_found=99, skipped_errors=99)

    job = scan_jobs.get_job(job_id)
    assert job["status"] == scan_jobs.STATUS_TIMED_OUT
    assert job["total"] == 0


def test_get_latest_job_for_account_returns_most_recent():
    account_id = _account()
    older = scan_jobs.create_scan_job(account_id)
    with get_connection() as conn:
        conn.execute(
            "UPDATE scan_jobs SET created_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), older),
        )
    newer = scan_jobs.create_scan_job(account_id)

    latest = scan_jobs.get_latest_job_for_account(account_id)
    assert latest["id"] == newer


def test_get_latest_job_for_account_returns_none_when_no_jobs():
    assert scan_jobs.get_latest_job_for_account(_account()) is None


def test_get_active_scan_job_returns_running_or_queued_only():
    account_id = _account()
    job_id = scan_jobs.create_scan_job(account_id)
    assert scan_jobs.get_active_scan_job(account_id)["id"] == job_id

    scan_jobs.mark_job_running(job_id)
    assert scan_jobs.get_active_scan_job(account_id)["id"] == job_id

    scan_jobs.mark_job_succeeded(job_id, total=1, candidates_found=0, skipped_errors=0)
    assert scan_jobs.get_active_scan_job(account_id) is None


def test_get_active_scan_job_reconciles_stale_running_job_first():
    account_id = _account()
    job_id = scan_jobs.create_scan_job(account_id)
    scan_jobs.mark_job_running(job_id)
    stale_start = datetime.now(timezone.utc) - timedelta(minutes=scan_jobs.STALE_RUNNING_MINUTES + 5)
    with get_connection() as conn:
        conn.execute("UPDATE scan_jobs SET started_at = ? WHERE id = ?", (stale_start.isoformat(), job_id))

    # Bu, YENİ bir tarama başlatılabileceği anlamına gelmeli — eski takılı
    # kalmış iş kalıcı kilidi sonsuza dek tutmamalı.
    assert scan_jobs.get_active_scan_job(account_id) is None
    assert scan_jobs.get_job(job_id)["status"] == scan_jobs.STATUS_TIMED_OUT


def test_get_active_scan_job_does_not_reconcile_recent_running_job():
    account_id = _account()
    job_id = scan_jobs.create_scan_job(account_id)
    scan_jobs.mark_job_running(job_id)

    assert scan_jobs.get_active_scan_job(account_id)["id"] == job_id
    assert scan_jobs.get_job(job_id)["status"] == scan_jobs.STATUS_RUNNING


def test_reconcile_orphaned_jobs_at_startup_fails_all_queued_and_running():
    account_id = _account()
    queued = scan_jobs.create_scan_job(account_id)
    running = scan_jobs.create_scan_job(account_id)
    scan_jobs.mark_job_running(running)
    done = scan_jobs.create_scan_job(account_id)
    scan_jobs.mark_job_running(done)
    scan_jobs.mark_job_succeeded(done, total=1, candidates_found=0, skipped_errors=0)

    affected = scan_jobs.reconcile_orphaned_jobs_at_startup()

    assert affected == 2
    assert scan_jobs.get_job(queued)["status"] == scan_jobs.STATUS_FAILED
    assert scan_jobs.get_job(running)["status"] == scan_jobs.STATUS_FAILED
    assert scan_jobs.get_job(done)["status"] == scan_jobs.STATUS_SUCCEEDED  # zaten bitmişti, dokunulmadı


def test_app_startup_reconciles_orphaned_jobs(temp_db, monkeypatch):
    # JOB-01: bir önceki (çökmüş/kapatılmış) süreçten kalan QUEUED/RUNNING
    # bir iş, sunucu yeniden başlayınca (bkz. app.py::lifespan) hemen
    # FAILED olarak işaretlenmeli — aksi halde hesap başına kalıcı kilit
    # sonsuza dek takılı kalır.
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)

    account_id = _account()
    orphaned_job_id = scan_jobs.create_scan_job(account_id)
    scan_jobs.mark_job_running(orphaned_job_id)

    from src.ui.app import app

    with TestClient(app):
        pass  # yalnızca lifespan'ın startup kısmını tetikle

    job = scan_jobs.get_job(orphaned_job_id)
    assert job["status"] == scan_jobs.STATUS_FAILED


class _DummyLLMProvider:
    def __init__(self, *args, **kwargs):
        pass

    def is_available(self):
        return True


class _DummyEmbeddingProvider:
    def __init__(self, *args, **kwargs):
        pass
