"""Provider-independent arayüzler (bkz. docs/architecture-plan.md §10/§14).

Uygulamanın geri kalanı (RAG, Rule Engine, Conversation Layer) bu arayüzleri
bilir; hangi somut runtime'ın (Foundry Local, Ollama, llama.cpp, ...) altta
çalıştığını bilmez. Provider seçimi bir factory/config üzerinden yapılır.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Chat/completion üreten yerel LLM runtime'ları için ortak arayüz."""

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context_chunks: list[str] | None = None,
        json_output: bool = False,
        allow_thinking: bool = False,
    ) -> str:
        """Sistem + kullanıcı promptu (ve varsa retrieve edilmiş bağlam) verilir,
        modelin ham metin yanıtı döner. ``json_output=True`` çağıran katmana
        modelin (destekliyorsa) JSON-uyumlu çıktı üretmesini ister — bu, extraction
        görevleri için hem gecikmeyi hem tutarlılığı önemli ölçüde iyileştirdiği
        ölçülen bir ayar (bkz. FoundryLocalProvider). Şema doğrulaması yine de
        burada değil, çağıran katmanda yapılır (bkz. §11 Rule Engine).
        ``allow_thinking=True``, reasoning modellerinde varsayılan `/no_think`
        bastırmasını kaldırır — canlı testte (GPU'da) bunun, hızdan ödün
        verilebilecek ama doğruluğun kritik olduğu görevlerde (örn. mail
        sınıflandırma) yanlış pozitifleri azalttığı ölçüldü; extraction gibi
        gecikmeye duyarlı görevlerde varsayılan False kalmalı."""

    @abstractmethod
    def is_available(self) -> bool:
        """Runtime/model şu an kullanılabilir mi (yüklü mü, servis ayakta mı)."""


class FileInputCapable(ABC):
    """Dosya girişi (fotoğraf, PDF, ...) destekleyen provider'lar için EK,
    opsiyonel arayüz — LLMProvider'IN KENDİSİNE bilerek eklenmedi. Bugün
    yalnızca GeminiProvider bunu destekliyor (varsayılan FoundryLocalProvider
    metin-only); intent/mail-sınıflandırma/policy gibi LLMProvider'ın HER
    çağıranına hiç kullanmayacakları bir metod dayatmak yerine, isteyen kod
    (bkz. src/services/chat_flow.py) `isinstance(llm, FileInputCapable)` ile
    kontrol edip yoksa zarifçe düşer (örn. "bu özellik yalnızca Gemini
    backend'i etkinken kullanılabilir").

    Görsel özelinde AYRI bir arayüz DEĞİL — Gemini API'de fotoğraf ve PDF
    aynı mekanizmayı (ham bayt + mime_type, bkz. `Part.from_bytes`)
    kullanıyor, isim ve imza bilerek dosya-türünden bağımsız tutuldu (bkz.
    src/services/vertical_prototype.py::_ACCEPTED_FILE_MIME_TYPES — hangi
    mime_type'ların gerçekten kabul edildiği tek yerde, çağıran katmanda)."""

    @abstractmethod
    def generate_from_file(
        self,
        system_prompt: str,
        user_prompt: str,
        file_bytes: bytes,
        mime_type: str,
        json_output: bool = False,
    ) -> str:
        """`LLMProvider.generate`'in dosya girişli karşılığı — context_chunks/
        allow_thinking YOK (bu çağrı yalnızca tek-atımlık dosyadan-çıkarım
        için, RAG bağlamı ya da reasoning modu ile birleştirilmiyor)."""


class EmbeddingProvider(ABC):
    """Metin embedding'i üreten yerel modeller için ortak arayüz."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Her metin için aynı boyutta bir embedding vektörü döner."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Üretilen vektörlerle birlikte saklanacak model adı (bkz. §14:
        embedding modeli değişirse hangi kayıtların yeniden embed edilmesi
        gerektiğini bulmak için model_name+dim her satırda tutulur)."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Üretilen vektörlerin boyutu."""
