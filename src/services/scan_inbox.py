"""Mail taraması (bkz. docs/architecture-plan.md §4).

Yeni mailleri çeker, her birini takvimlik olup olmadığına göre sınıflandırır,
takvimlik olanlardan candidate_event çıkarıp KUYRUĞA (candidate_events,
NEEDS_INFORMATION/READY_FOR_CONFIRMATION) yazar. Burada hiçbir onay/inceleme
YAPILMAZ — o artık Web UI'nin işi ("Gelen Öneriler" ekranı, bkz. src/ui/).
Önceki davranış (extraction sonrası hemen interaktif onaylatma,
review_and_confirm_candidate) yalnızca vertical_prototype.py'nin konuşma
akışında kaldı; mail taraması artık tamamen kuyruklama, kullanıcı onayı
tarayıcıdan gelir (kasıtlı davranış değişikliği — Web UI'nin gösterecek bir
şeyi olması bu ayrıma bağlıydı, "beklemede" candidate önceden hiç oluşmuyordu).

`scan_account_inbox` hem bu dosyanın CLI'sı (`main()`) hem Web UI'nin
"E-posta Hesapları" ekranındaki "Şimdi tara" butonu (bkz. src/ui/routes.py)
tarafından çağrılan paylaşılan çekirdek — CLI canlı ilerleme metni istiyor,
web istemiyor, bu yüzden `on_progress` opsiyonel bir callback (varsayılan:
sessiz)."""

from __future__ import annotations

from typing import Callable

from src.candidates.store import apply_update_suggestion, find_related_candidate_by_thread, save_new_candidate
from src.connectors.account_registry import select_account
from src.core.logging_config import configure_logging, get_logger
from src.providers.base import EmbeddingProvider, LLMProvider
from src.providers.foundry_local import FoundryLocalEmbeddingProvider, FoundryLocalProvider
from src.services.mail_analysis import analyze_possible_update, extract_candidate_from_email, is_calendar_worthy
from src.services.mail_sync import mark_email_processed, sync_new_emails
from src.services.vertical_prototype import apply_retrieved_policies
from src.storage.db import init_db

logger = get_logger("scan_inbox")


def scan_account_inbox(
    account_id: str,
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Bir hesabın gelen kutusunu tarar, takvimlik mailleri kuyruğa yazar.
    Döner: {"total": int, "candidates_found": int, "skipped_errors": int}."""

    def emit(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    emit("Yeni mailler kontrol ediliyor...")
    new_emails = sync_new_emails(account_id)
    emit(f"{len(new_emails)} yeni mail bulundu.\n")

    candidates_found = 0
    skipped_errors = 0
    for email_row_id, email in new_emails:
        try:
            worthy, reason = is_calendar_worthy(llm, embedding_provider, email)
        except Exception as e:
            # Model bazen boş/geçersiz JSON döndürüyor (canlı testte görüldü).
            # Tek bir sorunlu mail tüm taramayı çökertmemeli — bu maili
            # işlenmemiş bırakıp (sonraki taramada tekrar denenir) devam et.
            logger.warning("is_calendar_worthy failed for %r: %s", email.subject, e)
            emit(f"[atlandı] {email.subject[:60]!r} sınıflandırılamadı: {e}")
            skipped_errors += 1
            continue

        if not worthy:
            mark_email_processed(email_row_id)
            continue

        related = find_related_candidate_by_thread(email.thread_id, exclude_email_row_id=email_row_id)
        if related is not None:
            try:
                update_result = analyze_possible_update(llm, related["candidate"], email)
            except Exception as e:
                logger.warning("analyze_possible_update failed for %r: %s", email.subject, e)
                emit(f"[atlandı] \"{email.subject[:60]}\" güncelleme analizi başarısız oldu: {e}\n")
                skipped_errors += 1
                continue
            if update_result["is_update"]:
                apply_update_suggestion(
                    related["candidate"].candidate_id, update_result["changed_fields"], email_row_id
                )
                mark_email_processed(email_row_id)
                emit(f"--- Güncelleme önerisi olarak işlendi: {email.subject} ---\n")
                continue
            # is_update=False: aynı thread ama alakasız konu — normal (bağımsız) akışa devam.

        try:
            candidate = extract_candidate_from_email(llm, email)
        except Exception as e:
            logger.warning("extract_candidate_from_email failed for %r: %s", email.subject, e)
            emit(f"[atlandı] \"{email.subject[:60]}\" çıkarımı başarısız oldu: {e}\n")
            skipped_errors += 1
            continue

        apply_retrieved_policies(candidate, embedding_provider, sender=email.sender)
        save_new_candidate(candidate, source_email_row_id=email_row_id)
        mark_email_processed(email_row_id)

        candidates_found += 1
        emit(f"--- Kuyruğa eklendi ({candidates_found}) ---")
        emit(f"Konu:            {email.subject}")
        emit(f"Gönderen:        {email.sender}")
        emit(f"Neden önerildi:  {reason}")
        emit(f"Durum:           {candidate.status}\n")

    return {"total": len(new_emails), "candidates_found": candidates_found, "skipped_errors": skipped_errors}


def main() -> None:
    configure_logging()
    init_db()
    account_id, _account_email = select_account()
    # prefer_gpu=False: mail taraması onlarca ardışık LLM çağrısı yapıyor
    # (her mail için sınıflandırma + gerekirse extraction); canlı testte GPU
    # yolunda birkaç mail sonra "CUDA error ... illegal memory access"
    # (Foundry Local'ın native katmanında kalıcı bir CUDA context bozulması)
    # görüldü ve süreç geri kalan TÜM maillerde aynı şekilde çökmeye devam
    # etti. CPU daha yavaş ama taramanın tamamını güvenilir şekilde bitiriyor.
    # embedding_provider ve vertical_prototype.py'nin LLM'i GPU'da kalmaya
    # devam ediyor (çok daha az ardışık çağrı yapıyorlar, aynı riski taşımıyor).
    llm = FoundryLocalProvider(model_alias="qwen3-4b", prefer_gpu=False)
    embedding_provider = FoundryLocalEmbeddingProvider()

    result = scan_account_inbox(account_id, llm, embedding_provider, on_progress=print)

    if result["skipped_errors"]:
        print(f"({result['skipped_errors']} mail işlenemedi, sonraki taramada tekrar denenecek.)\n")

    if result["candidates_found"] == 0:
        print("Takvimlik bir şey bulamadım.")
    else:
        print(f"{result['candidates_found']} öneri web arayüzünde (Gelen Öneriler) sizi bekliyor.")


if __name__ == "__main__":
    main()
