"""`src/providers/gemini.py` için testler — gerçek Gemini API'ye ASLA
çağrı yapılmaz, `google.genai.Client` sahte bir sınıfla değiştiriliyor
(bkz. conftest.py::_no_real_cloud_llm — bu ayrıca her testte ortam
değişkenlerini temizleyerek geliştiricinin gerçek .env'inin sızmasını
engelliyor)."""

from __future__ import annotations

import pytest

from src.providers.gemini import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL,
    GeminiEmbeddingProvider,
    GeminiProvider,
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeEmbedResponse:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeModels:
    def __init__(self, text="merhaba", embedding=None):
        self.last_generate_kwargs = None
        self.last_embed_kwargs = None
        self._text = text
        self._embedding = embedding or [0.1, 0.2, 0.3]

    def generate_content(self, **kwargs):
        self.last_generate_kwargs = kwargs
        return _FakeResponse(self._text)

    def embed_content(self, **kwargs):
        self.last_embed_kwargs = kwargs
        contents = kwargs["contents"]
        return _FakeEmbedResponse([_FakeEmbedding(self._embedding) for _ in contents])


class _FakeClient:
    last_api_key = None

    def __init__(self, api_key=None):
        _FakeClient.last_api_key = api_key
        self.models = _FakeModels()


@pytest.fixture
def fake_genai(monkeypatch):
    monkeypatch.setattr("src.providers.gemini.genai.Client", _FakeClient)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123")
    return _FakeClient


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr("src.providers.gemini.genai.Client", _FakeClient)
    with pytest.raises(RuntimeError):
        GeminiProvider()


def test_gemini_api_key_env_var_also_accepted(monkeypatch):
    monkeypatch.setattr("src.providers.gemini.genai.Client", _FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "alt-key")
    GeminiProvider()
    assert _FakeClient.last_api_key == "alt-key"


def test_generate_uses_default_model_and_passes_system_instruction(fake_genai):
    provider = GeminiProvider()
    result = provider.generate("bir sistem promptu", "merhaba")
    assert result == "merhaba"
    kwargs = provider._client.models.last_generate_kwargs
    assert kwargs["model"] == DEFAULT_CHAT_MODEL
    assert kwargs["contents"] == "merhaba"
    assert kwargs["config"].system_instruction == "bir sistem promptu"


def test_generate_json_output_sets_response_mime_type(fake_genai):
    provider = GeminiProvider()
    provider.generate("sistem", "kullanıcı", json_output=True)
    config = provider._client.models.last_generate_kwargs["config"]
    assert config.response_mime_type == "application/json"


def test_generate_default_disables_thinking(fake_genai):
    provider = GeminiProvider()
    provider.generate("sistem", "kullanıcı")
    config = provider._client.models.last_generate_kwargs["config"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0


def test_generate_allow_thinking_omits_thinking_config(fake_genai):
    provider = GeminiProvider()
    provider.generate("sistem", "kullanıcı", allow_thinking=True)
    config = provider._client.models.last_generate_kwargs["config"]
    assert config.thinking_config is None


def test_generate_context_chunks_include_untrusted_preamble(fake_genai):
    provider = GeminiProvider()
    provider.generate("sistem", "asıl soru", context_chunks=["mail içeriği burada"])
    contents = provider._client.models.last_generate_kwargs["contents"]
    assert "güvenilmeyen" in contents
    assert "mail içeriği burada" in contents
    assert "asıl soru" in contents
    assert contents.index("mail içeriği burada") < contents.index("asıl soru")


def test_is_available_reflects_api_key_presence(fake_genai, monkeypatch):
    provider = GeminiProvider()
    assert provider.is_available() is True
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert provider.is_available() is False


def test_embedding_provider_returns_vectors(fake_genai):
    provider = GeminiEmbeddingProvider()
    vectors = provider.embed(["bir", "iki"])
    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2, 0.3]
    assert provider.model_name == DEFAULT_EMBEDDING_MODEL
    assert provider.dimension == DEFAULT_EMBEDDING_DIMENSION


def test_embedding_provider_empty_list_skips_api_call(fake_genai):
    provider = GeminiEmbeddingProvider()
    assert provider.embed([]) == []
    assert provider._client.models.last_embed_kwargs is None
