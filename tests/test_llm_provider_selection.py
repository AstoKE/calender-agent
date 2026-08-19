"""`src/ui/app.py`'nin lifespan'ındaki LLM_PROVIDER seçimi için test —
varsayılan (env değişkeni yok) Foundry Local'ı, `LLM_PROVIDER=gemini` Gemini'yi
kullanmalı. Gerçek hiçbir provider inşa edilmez, ikisi de sahtelerle
değiştiriliyor (bkz. conftest.py::_no_real_cloud_llm, her testte
LLM_PROVIDER/GOOGLE_API_KEY otomatik temizleniyor — burada testin kendisi
bilinçli olarak yeniden set ediyor)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.providers.base import EmbeddingProvider, LLMProvider


class _DummyLLMProvider(LLMProvider):
    def __init__(self, *args, **kwargs):
        pass

    def generate(self, system_prompt, user_prompt, context_chunks=None, json_output=False, allow_thinking=False):
        return "{}" if json_output else ""

    def is_available(self):
        return True


class _DummyEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *args, **kwargs):
        pass

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    @property
    def model_name(self):
        return "dummy-embedding"

    @property
    def dimension(self):
        return 8


class _DummyGeminiProvider(_DummyLLMProvider):
    pass


class _DummyGeminiEmbeddingProvider(_DummyEmbeddingProvider):
    pass


def test_default_backend_uses_foundry_local(temp_db, monkeypatch):
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.app.GeminiProvider", _DummyGeminiProvider)
    monkeypatch.setattr("src.ui.app.GeminiEmbeddingProvider", _DummyGeminiEmbeddingProvider)
    from src.ui.app import app

    with TestClient(app):
        assert isinstance(app.state.llm, _DummyLLMProvider)
        assert not isinstance(app.state.llm, _DummyGeminiProvider)
        assert isinstance(app.state.embedding_provider, _DummyEmbeddingProvider)


def test_gemini_backend_selected_via_env_var(temp_db, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.app.GeminiProvider", _DummyGeminiProvider)
    monkeypatch.setattr("src.ui.app.GeminiEmbeddingProvider", _DummyGeminiEmbeddingProvider)
    from src.ui.app import app

    with TestClient(app):
        assert isinstance(app.state.llm, _DummyGeminiProvider)
        assert isinstance(app.state.embedding_provider, _DummyGeminiEmbeddingProvider)


def test_backend_selection_is_case_insensitive(temp_db, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "GEMINI")
    monkeypatch.setattr("src.ui.app.FoundryLocalProvider", _DummyLLMProvider)
    monkeypatch.setattr("src.ui.app.FoundryLocalEmbeddingProvider", _DummyEmbeddingProvider)
    monkeypatch.setattr("src.ui.app.GeminiProvider", _DummyGeminiProvider)
    monkeypatch.setattr("src.ui.app.GeminiEmbeddingProvider", _DummyGeminiEmbeddingProvider)
    from src.ui.app import app

    with TestClient(app):
        assert isinstance(app.state.llm, _DummyGeminiProvider)
