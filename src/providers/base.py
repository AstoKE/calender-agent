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
    ) -> str:
        """Sistem + kullanıcı promptu (ve varsa retrieve edilmiş bağlam) verilir,
        modelin ham metin yanıtı döner. Yapılandırılmış çıktı üreten çağrılar
        (örn. JSON şeması) bu metnin üzerine ayrıca şema doğrulaması yapar —
        doğrulama burada değil, çağıran katmanda (bkz. §11 Rule Engine)."""

    @abstractmethod
    def is_available(self) -> bool:
        """Runtime/model şu an kullanılabilir mi (yüklü mü, servis ayakta mı)."""


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
