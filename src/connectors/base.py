"""Sağlayıcıdan bağımsız connector arayüzleri (bkz. docs/architecture-plan.md §13).

Uygulamanın geri kalanı bu arayüzleri bilir; Gmail/Outlook veya Google/MS
Calendar'a özgü API çağrıları yalnızca somut implementasyonlarda (örn.
src/connectors/gmail.py) yer alır.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol

from src.core.models import UnifiedEmail


class EmailConnector(ABC):
    @abstractmethod
    def list_new_messages(self, since_cursor: str | None) -> tuple[list[UnifiedEmail], str]:
        """``since_cursor`` None ise ilk senkronizasyon (son N mesaj); değilse
        sağlayıcının kendi incremental cursor'ı (örn. Gmail historyId) ile yeni
        mesajlar getirilir. Dönüş: (yeni mesajlar, bir sonraki cursor)."""

    @abstractmethod
    def get_message(self, message_id: str) -> UnifiedEmail:
        """Tek bir mesajı tam içeriğiyle (body dahil) getirir."""


class AttachmentDownloadable(Protocol):
    """`GmailConnector`/`OutlookConnector`'ın İKİSİNİN de uyguladığı ama
    `EmailConnector`'ın abstract arayüzünde OLMAYAN ek metot (yalnızca ek
    dosya indirme özelliği olan mail_analysis.py::
    extract_candidate_from_email_with_attachments'ın ihtiyacı var, her
    connector'ın değil) — bkz. Attachment.attachment_id docstring'i
    (sıfır kalıcılık ilkesi, bayt içeriği hiçbir yere yazılmaz)."""

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes: ...


class CalendarConnector(ABC):
    @abstractmethod
    def list_events(self, time_min: datetime, time_max: datetime, calendar_id: str = "primary") -> list[dict]:
        """Belirtilen aralıktaki etkinlikleri sağlayıcının ham temsiliyle döner
        (Normalization Layer bunu calendar_events_cache şemasına çevirir)."""

    @abstractmethod
    def get_freebusy(self, time_min: datetime, time_max: datetime, calendar_id: str = "primary") -> list[tuple[datetime, datetime]]:
        """Meşgul aralıkların listesini döner (çakışma kontrolü için, bkz. Availability Engine)."""

    @abstractmethod
    def create_event(
        self,
        *,
        title: str | None,
        start: datetime,
        end: datetime,
        location: str | None = None,
        calendar_id: str = "primary",
    ) -> str:
        """Etkinliği oluşturur, sağlayıcının event_id'sini döner. Yalnızca
        Calendar Action Executor tarafından, onay sonrası çağrılmalıdır.

        Sağlayıcıdan BAĞIMSIZ, düz alanlar (bkz. §13) — Google'ın
        ({"summary":..., "start": {"dateTime":...}}) ya da Microsoft
        Graph'ın kendi JSON şekli yalnızca somut implementasyonun İÇİNDE
        kurulur, çağıran katman hiçbirini bilmez (bkz. Outlook entegrasyonu
        öncesi düzeltilen normalizasyon sızıntısı, CLAUDE.md)."""

    @abstractmethod
    def update_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        location: str | None = None,
        calendar_id: str = "primary",
    ) -> None:
        """Var olan bir etkinliği günceller. Yalnızca onay sonrası çağrılmalıdır.
        `None` bırakılan alanlar DEĞİŞTİRİLMEZ (kısmi güncelleme/PATCH
        semantiği) — örn. yalnızca `start`/`end` verilip bir etkinliği
        taşımak, başlığı/konumu hiç etkilemez."""

    @abstractmethod
    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None:
        """Var olan bir etkinliği siler. Yalnızca onay sonrası çağrılmalıdır."""
