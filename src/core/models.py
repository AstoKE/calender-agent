"""Domain modelleri (bkz. docs/architecture-plan.md §14).

Bu modüldeki şemalar uygulamanın ortak iç veri modelidir: Gmail/Outlook veya
Google/MS Calendar'a özgü alanlar bu katmanın üzerine hiçbir zaman sızmaz —
connector'lar (src/connectors) sağlayıcı verisini bu modellere normalize eder.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class EmailProvider(str, Enum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"


class EventType(str, Enum):
    MEETING = "meeting"
    APPOINTMENT = "appointment"
    EXAM = "exam"
    DEADLINE = "deadline"
    TRAVEL = "travel"
    RESERVATION = "reservation"
    PERSONAL_COMMITMENT = "personal_commitment"
    OTHER = "other"


class Importance(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class CandidateStatus(str, Enum):
    DETECTED = "DETECTED"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    READY_FOR_CONFIRMATION = "READY_FOR_CONFIRMATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ADDED_TO_CALENDAR = "ADDED_TO_CALENDAR"
    UPDATE_SUGGESTED = "UPDATE_SUGGESTED"
    UPDATED_IN_CALENDAR = "UPDATED_IN_CALENDAR"
    DISMISSED = "DISMISSED"


class PolicyScope(str, Enum):
    GLOBAL = "global"
    EVENT_TYPE = "event_type"
    SENDER = "sender"
    ACCOUNT = "account"


class PolicySource(str, Enum):
    MANUAL = "manual"
    CORRECTION = "correction"


class CorrectionScope(str, Enum):
    SINGLE_EVENT = "single_event"
    EVENT_TYPE = "event_type"
    SENDER = "sender"
    ACCOUNT = "account"


class SourceType(str, Enum):
    CONVERSATION = "conversation"
    EMAIL = "email"


class IntentType(str, Enum):
    """Conversation Layer'ın kullanıcı mesajına atadığı üst düzey niyet
    (bkz. docs/architecture-plan.md §6/§20 Hafta2)."""

    CREATE_EVENT = "create_event"
    QUERY_CALENDAR = "query_calendar"
    UPDATE_EVENT = "update_event"
    DEFINE_POLICY = "define_policy"
    OTHER = "other"


class Attachment(BaseModel):
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    # Sağlayıcının (Gmail) kendi iç kimliği — GERÇEK bayt içeriği değil, yalnızca
    # `GmailConnector.download_attachment`'ı çağırmak için gereken bir referans.
    # Bu model (ve dolayısıyla bu alan) hiçbir yerde kalıcı SAKLANMIYOR —
    # `UnifiedEmail` gibi yalnızca bir tarama turunun ömrü boyunca bellekte
    # yaşıyor (bkz. src/services/mail_analysis.py::extract_candidate_from_email_with_attachments).
    attachment_id: Optional[str] = None


class Participant(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None


class ReminderSpec(BaseModel):
    minutes_before: int


class UnifiedEmail(BaseModel):
    """Sağlayıcıya özgü mail verisinin ortak iç temsili.

    `body_text` yalnızca candidate çözülene kadar geçici tutulur; kalıcı
    saklama minimum retention ilkesine aykırıdır (bkz. §12/§13).
    """

    model_config = ConfigDict(use_enum_values=True)

    provider: EmailProvider
    account_id: str
    message_id: str
    thread_id: str
    subject: str
    sender: str
    recipients: list[str] = Field(default_factory=list)
    received_at: datetime
    detected_language: Optional[str] = None
    body_text: Optional[str] = None
    attachments: list[Attachment] = Field(default_factory=list)
    source_url_or_reference: Optional[str] = None
    labels: list[str] = Field(
        default_factory=list,
        description=(
            "Sağlayıcının kendi kategorileri (örn. Gmail'in CATEGORY_PROMOTIONS/"
            "CATEGORY_SOCIAL etiketleri). Reklam/bülten filtrelemesinde deterministik "
            "bir ön kontrol için kullanılır — bkz. src/services/mail_analysis.py."
        ),
    )


class CandidateEvent(BaseModel):
    # validate_assignment: eksik/belirsiz alanlar doldurulurken (bkz.
    # src/services/vertical_prototype.py fill_missing_fields_interactively)
    # candidate.start_datetime gibi alanlara doğrudan atama yapılıyor; bu
    # olmadan atanan ham string bir daha datetime'a çevrilmeden kalıyordu.
    model_config = ConfigDict(use_enum_values=True, validate_assignment=True)

    candidate_id: str
    source_type: SourceType
    source_references: list[str] = Field(default_factory=list)
    source_languages: list[str] = Field(default_factory=list)
    event_type: EventType
    title: Optional[str] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    timezone: Optional[str] = None
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    online_meeting_url: Optional[str] = None
    participants: list[Participant] = Field(default_factory=list)
    description: Optional[str] = None
    importance: Optional[Importance] = None
    reminders: list[ReminderSpec] = Field(default_factory=list)
    preparation_time_minutes: Optional[int] = None
    travel_time_minutes: Optional[int] = None
    recurrence: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    status: CandidateStatus = CandidateStatus.DETECTED
    extraction_reason: Optional[str] = None
    retrieved_policy_ids: list[str] = Field(default_factory=list)
    retrieved_correction_ids: list[str] = Field(default_factory=list)


class PersonalPolicy(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    policy_id: str
    category: str
    scope: PolicyScope
    natural_language_rule: str
    language: str
    structured_conditions: dict = Field(default_factory=dict)
    structured_action: dict = Field(default_factory=dict)
    priority: int = 0
    version: int = 1
    active: bool = True
    approved_by_user: bool = True
    source: PolicySource
    created_at: datetime
    updated_at: datetime
    user_id: Optional[str] = None  # bkz. plan "Per-user isolation" — None: eski/sahipsiz kayıt ya da CLI


class UserCorrection(BaseModel):
    """Kullanıcı düzeltmesi kaydı.

    Plan dokümanının §7 (önerilen kayıt) ve §14 (User correction) bölümlerindeki
    iki şema burada birleştirilmiştir: scope alanları (event_type/account_scope/
    sender_scope) düzeltmenin hangi kapsamda politikaya dönüşeceğini belirlemek
    için gereklidir (bkz. §10 Adaptive Personal RAG).
    """

    model_config = ConfigDict(use_enum_values=True)

    correction_id: str
    candidate_id: str
    original_input: Optional[str] = None
    original_output: dict
    user_feedback_text: str
    corrected_output: dict
    correction_scope: CorrectionScope = CorrectionScope.SINGLE_EVENT
    event_type: Optional[EventType] = None
    account_scope: Optional[str] = None
    sender_scope: Optional[str] = None
    language: str
    approved_for_future_use: bool = False
    derived_policy_id: Optional[str] = None
    created_at: datetime
    user_id: Optional[str] = None  # bkz. plan "Per-user isolation" — None: eski/sahipsiz kayıt ya da CLI
