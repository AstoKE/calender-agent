"""Mail taraması (bkz. docs/architecture-plan.md §4).

Yeni mailleri çeker, her birini takvimlik olup olmadığına göre sınıflandırır,
takvimlik olanlardan candidate_event çıkarıp mevcut onay/çakışma/yazma
akışını (review_and_confirm_candidate) yeniden kullanarak kullanıcıya sunar.

Uygulama açılışında çalıştırılması öngörülen akış (§16 ana ekran "Son mail
analizi özeti"); şu an ayrı bir CLI komutu (`python -m src.services.scan_inbox`).
"""

from __future__ import annotations

from src.connectors.account_registry import ACCOUNT_EMAIL, ACCOUNT_ID, ensure_account_registered
from src.connectors.google_calendar import GoogleCalendarConnector
from src.providers.foundry_local import FoundryLocalEmbeddingProvider, FoundryLocalProvider
from src.services.mail_analysis import extract_candidate_from_email, is_calendar_worthy
from src.services.mail_sync import mark_email_processed, sync_new_emails
from src.services.vertical_prototype import review_and_confirm_candidate
from src.storage.db import init_db


def main() -> None:
    init_db()
    ensure_account_registered(ACCOUNT_ID, provider="google", email=ACCOUNT_EMAIL)
    llm = FoundryLocalProvider(model_alias="qwen3-4b")
    embedding_provider = FoundryLocalEmbeddingProvider()
    calendar = GoogleCalendarConnector(account_id=ACCOUNT_ID)

    print("Yeni mailler kontrol ediliyor...")
    new_emails = sync_new_emails(ACCOUNT_ID)
    print(f"{len(new_emails)} yeni mail bulundu.\n")

    candidates_found = 0
    for email_row_id, email in new_emails:
        worthy, reason = is_calendar_worthy(llm, email)
        if not worthy:
            mark_email_processed(email_row_id)
            continue

        candidates_found += 1
        print(f"--- Takvimlik mail bulundu ({candidates_found}) ---")
        print(f"Konu:            {email.subject}")
        print(f"Gönderen:        {email.sender}")
        print(f"Neden önerildi:  {reason}\n")

        candidate = extract_candidate_from_email(llm, email)
        review_and_confirm_candidate(
            candidate, calendar, embedding_provider, source_email_row_id=email_row_id
        )
        mark_email_processed(email_row_id)
        print()

    if candidates_found == 0:
        print("Takvime eklenmeye değer bir şey bulamadım.")


if __name__ == "__main__":
    main()
