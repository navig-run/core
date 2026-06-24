"""
Unit tests for navig/memory/embeddings.py

Covers: EmbeddingConfig, EmbeddingProvider.similarity, LocalEmbeddingProvider,
        OpenAIEmbeddingProvider, CachedEmbeddingProvider, get_embedding_provider
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.memory.embeddings import (
    CachedEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)


# ──────────────────────────────────────────────────────────────────────
# EmbeddingConfig defaults
# ──────────────────────────────────────────────────────────────────────


class TestEmbeddingConfig:
    def test_defaults(self):
        cfg = EmbeddingConfig()
        assert cfg.provider == "local"
        assert cfg.model == "all-MiniLM-L6-v2"
        assert cfg.cache_dir is None
        assert cfg.api_key is None
        assert cfg.dimension == 384
        assert cfg.batch_size == 32

    def test_override(self):
        cfg = EmbeddingConfig(provider="openai", model="text-embedding-3-small", dimension=1536)
        assert cfg.provider == "openai"
        assert cfg.model == "text-embedding-3-small"
        assert cfg.dimension == 1536


# ──────────────────────────────────────────────────────────────────────
# EmbeddingProvider.similarity (pure math, no external deps needed)
# ──────────────────────────────────────────────────────────────────────


class ConcreteProvider(EmbeddingProvider):
    """Minimal concrete subclass for testing base methods."""

    @property
    def dimension(self) -> int:
        return 3

    def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


class TestSimilarity:
    def setup_method(self):
        self.provider = ConcreteProvider()

    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        sim = self.provider.similarity(v, v)
        assert abs(sim - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self):
        sim = self.provider.similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        assert abs(sim) < 1e-6

    def test_opposite_vectors_return_minus_one(self):
        sim = self.provider.similarity([1.0, 0.0], [-1.0, 0.0])
        assert abs(sim - (-1.0)) < 1e-6

    def test_empty_vectors_return_zero(self):
        assert self.provider.similarity([], []) == 0.0

    def test_zero_vector_returns_zero(self):
        assert self.provider.similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            self.provider.similarity([1.0, 2.0], [1.0])

    def test_known_cosine_value(self):
        # [1,1] vs [1,0] → cos(45°) ≈ 0.7071
        sim = self.provider.similarity([1.0, 1.0], [1.0, 0.0])
        assert abs(sim - 1.0 / math.sqrt(2)) < 1e-4


# ──────────────────────────────────────────────────────────────────────
# LocalEmbeddingProvider
# ──────────────────────────────────────────────────────────────────────


class TestLocalEmbeddingProvider:
    def test_known_model_dimensions(self):
        dims = LocalEmbeddingProvider.MODEL_DIMENSIONS
        assert dims["all-MiniLM-L6-v2"] == 384
        assert dims["all-mpnet-base-v2"] == 768
        assert dims["all-MiniLM-L12-v2"] == 384

    def test_default_model_dimension(self):
        p = LocalEmbeddingProvider()
        assert p.dimension == 384

    def test_custom_model_defaults_to_384(self):
        p = LocalEmbeddingProvider(model_name="unknown-model")
        assert p.dimension == 384

    def test_mpnet_model_dimension(self):
        p = LocalEmbeddingProvider(model_name="all-mpnet-base-v2")
        assert p.dimension == 768

    def test_model_not_loaded_initially(self):
        p = LocalEmbeddingProvider()
        assert p._model is None

    def test_embed_text_raises_without_sentence_transformers(self):
        p = LocalEmbeddingProvider()
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            with pytest.raises((ImportError, Exception)):
                p.embed_text("hello")

    def test_embed_batch_empty_returns_empty(self):
        p = LocalEmbeddingProvider()
        mock_model = MagicMock()
        p._model = mock_model
        mock_model.encode.return_value = MagicMock(tolist=lambda: [])
        # embed_batch with empty list returns early without calling model
        result = p.embed_batch([])
        assert result == []
        mock_model.encode.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# OpenAIEmbeddingProvider
# ──────────────────────────────────────────────────────────────────────


class TestOpenAIEmbeddingProvider:
    def test_known_model_dimensions(self):
        dims = OpenAIEmbeddingProvider.MODEL_DIMENSIONS
        assert dims["text-embedding-3-small"] == 1536
        assert dims["text-embedding-3-large"] == 3072
        assert dims["text-embedding-ada-002"] == 1536

    def test_raises_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            # Make sure OPENAI_API_KEY not set
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            with pytest.raises(ValueError, match="API key required"):
                OpenAIEmbeddingProvider()

    def test_dimension_small_model(self):
        p = OpenAIEmbeddingProvider(api_key="sk-test")
        assert p.dimension == 1536

    def test_dimension_large_model(self):
        p = OpenAIEmbeddingProvider(api_key="sk-test", model="text-embedding-3-large")
        assert p.dimension == 3072

    def test_dimension_unknown_model_defaults_to_1536(self):
        p = OpenAIEmbeddingProvider(api_key="sk-test", model="custom-model")
        assert p.dimension == 1536

    def test_client_not_loaded_initially(self):
        p = OpenAIEmbeddingProvider(api_key="sk-test")
        assert p._client is None

    def test_embed_batch_empty_returns_empty(self):
        p = OpenAIEmbeddingProvider(api_key="sk-test")
        result = p.embed_batch([])
        assert result == []


# ──────────────────────────────────────────────────────────────────────
# CachedEmbeddingProvider
# ──────────────────────────────────────────────────────────────────────


class TestCachedEmbeddingProvider:
    def _mock_base(self, vec=None):
        base = MagicMock(spec=EmbeddingProvider)
        base.dimension = 3
        if vec is None:
            vec = [1.0, 0.0, 0.0]
        base.embed_text.return_value = vec
        base.embed_batch.return_value = [vec]
        return base

    def test_dimension_delegates_to_base(self, tmp_path):
        base = self._mock_base()
        c = CachedEmbeddingProvider(base, cache_dir=tmp_path)
        assert c.dimension == 3

    def test_embed_text_calls_base_on_miss(self, tmp_path):
        base = self._mock_base([0.1, 0.2, 0.3])
        c = CachedEmbeddingProvider(base, cache_dir=tmp_path)
        result = c.embed_text("hello")
        assert result == [0.1, 0.2, 0.3]
        base.embed_text.assert_called_once_with("hello")

    def test_embed_text_uses_cache_on_second_call(self, tmp_path):
        base = self._mock_base([0.1, 0.2, 0.3])
        c = CachedEmbeddingProvider(base, cache_dir=tmp_path)
        c.embed_text("hello")
        c.embed_text("hello")
        # base.embed_text called only once
        assert base.embed_text.call_count == 1

    def test_cache_persisted_to_disk(self, tmp_path):
        base = self._mock_base([0.5, 0.5, 0.5])
        c = CachedEmbeddingProvider(base, cache_dir=tmp_path)
        c.embed_text("persist me")
        cache_file = tmp_path / "embeddings_cache.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert len(data) == 1

    def test_cache_loaded_on_init(self, tmp_path):
        # Write a cache file first
        vec = [0.9, 0.1, 0.0]
        import hashlib
        key = hashlib.md5("preloaded".encode()).hexdigest()
        cache_file = tmp_path / "embeddings_cache.json"
        cache_file.write_text(json.dumps({key: vec}))

        base = self._mock_base()
        c = CachedEmbeddingProvider(base, cache_dir=tmp_path)
        result = c.embed_text("preloaded")
        assert result == vec
        base.embed_text.assert_not_called()

    def test_embed_batch_empty_returns_empty(self, tmp_path):
        base = self._mock_base()
        c = CachedEmbeddingProvider(base, cache_dir=tmp_path)
        # Empty batch — wrapped provider handles it
        base.embed_batch.return_value = []
        result = c.embed_batch([])
        assert result == []


# ──────────────────────────────────────────────────────────────────────
# get_embedding_provider factory
# ──────────────────────────────────────────────────────────────────────


class TestGetEmbeddingProvider:
    def test_local_returns_local_provider(self):
        cfg = EmbeddingConfig(provider="local")
        p = get_embedding_provider(cfg)
        assert isinstance(p, LocalEmbeddingProvider)

    def test_local_with_cache_dir_returns_cached(self, tmp_path):
        cfg = EmbeddingConfig(provider="local", cache_dir=tmp_path)
        p = get_embedding_provider(cfg)
        assert isinstance(p, CachedEmbeddingProvider)

    def test_openai_returns_openai_provider(self):
        cfg = EmbeddingConfig(provider="openai", api_key="sk-test")
        p = get_embedding_provider(cfg)
        assert isinstance(p, OpenAIEmbeddingProvider)

    def test_custom_model_passed_through(self):
        cfg = EmbeddingConfig(provider="local", model="all-mpnet-base-v2")
        p = get_embedding_provider(cfg)
        assert isinstance(p, LocalEmbeddingProvider)
        assert p.model_name == "all-mpnet-base-v2"
