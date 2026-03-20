"""
Vector embedding providers for semantic search.

Supports local (sentence-transformers) and remote (OpenAI) embeddings.
Local is preferred for privacy; remote as fallback or for higher quality.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger
        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:
        pass  # Fail silently if logging unavailable


@dataclass
class EmbeddingConfig:
    """Configuration for embedding providers."""

    provider: str = "local"  # local, openai
    model: str = "all-MiniLM-L6-v2"
    cache_dir: Optional[Path] = None
    api_key: Optional[str] = None
    dimension: int = 384  # Default for MiniLM
    batch_size: int = 32


class EmbeddingProvider(ABC):
    """
    Abstract base class for embedding providers.
    
    Embeddings convert text to fixed-size vectors for similarity search.
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimension size."""
        pass

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.
        
        Args:
            text: Input text
            
        Returns:
            Float vector of dimension size
        """
        pass

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of input texts
            
        Returns:
            List of float vectors
        """
        pass

    def similarity(
        self,
        vec1: list[float],
        vec2: list[float],
    ) -> float:
        """
        Compute cosine similarity between two vectors.
        
        Args:
            vec1: First vector
            vec2: Second vector
            
        Returns:
            Similarity score between -1 and 1
        
        Raises:
            ImportError: If numpy is not installed
        """
        if not HAS_NUMPY:
            raise ImportError(
                "numpy is required for vector similarity. "
                "Install it with: pip install numpy"
            )
        a = np.array(vec1)
        b = np.array(vec2)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))


class LocalEmbeddingProvider(EmbeddingProvider):
    """
    Local embedding provider using sentence-transformers.
    
    Runs entirely on-device, no API calls needed.
    Models are cached in ~/.cache/torch/sentence_transformers/
    
    Usage:
        provider = LocalEmbeddingProvider()
        vec = provider.embed_text("Hello world")
    """

    # Model dimension mappings
    MODEL_DIMENSIONS = {
        'all-MiniLM-L6-v2': 384,
        'all-mpnet-base-v2': 768,
        'all-MiniLM-L12-v2': 384,
        'paraphrase-MiniLM-L6-v2': 384,
        'multi-qa-mpnet-base-dot-v1': 768,
    }

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        cache_dir: Optional[Path] = None,
        device: Optional[str] = None,
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._device = device
        self._model = None
        self._dimension = self.MODEL_DIMENSIONS.get(model_name, 384)

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_model(self):
        """Lazy-load the model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                kwargs = {}
                if self.cache_dir:
                    kwargs['cache_folder'] = str(self.cache_dir)

                self._model = SentenceTransformer(self.model_name, **kwargs)

                if self._device:
                    self._model.to(self._device)

                _debug_log(f"Loaded embedding model: {self.model_name}")

            except ImportError as _exc:
                raise ImportError(
                    "sentence-transformers is required for local embeddings. "
                    "Install with: pip install sentence-transformers"
                ) from _exc

        return self._model

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for text."""
        model = self._get_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for batch of texts."""
        if not texts:
            return []

        model = self._get_model()
        embeddings = model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 100,
        )
        return embeddings.tolist()


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    OpenAI embedding provider.
    
    Requires OPENAI_API_KEY environment variable or explicit key.
    Uses text-embedding-3-small by default (1536 dimensions).
    
    Usage:
        provider = OpenAIEmbeddingProvider(api_key="sk-...")
        vec = provider.embed_text("Hello world")
    """

    MODEL_DIMENSIONS = {
        'text-embedding-3-small': 1536,
        'text-embedding-3-large': 3072,
        'text-embedding-ada-002': 1536,
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-small",
        base_url: Optional[str] = None,
    ):
        import os

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.base_url = base_url
        self._dimension = self.MODEL_DIMENSIONS.get(model, 1536)
        self._client = None

        if not self.api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY environment "
                "variable or pass api_key parameter."
            )

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI

                kwargs = {'api_key': self.api_key}
                if self.base_url:
                    kwargs['base_url'] = self.base_url

                self._client = OpenAI(**kwargs)
                _debug_log("Initialized OpenAI client for embeddings")

            except ImportError as _exc:
                raise ImportError(
                    "openai package is required for OpenAI embeddings. "
                    "Install with: pip install openai"
                ) from _exc

        return self._client

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for text."""
        client = self._get_client()

        response = client.embeddings.create(
            model=self.model,
            input=text,
        )

        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for batch of texts."""
        if not texts:
            return []

        client = self._get_client()

        # OpenAI handles batching internally (up to 2048 inputs)
        response = client.embeddings.create(
            model=self.model,
            input=texts,
        )

        # Sort by index to maintain order
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]


class CachedEmbeddingProvider(EmbeddingProvider):
    """
    Wrapper that caches embeddings to disk.
    
    Useful for avoiding repeated computation of the same texts.
    
    Usage:
        base = LocalEmbeddingProvider()
        cached = CachedEmbeddingProvider(base, cache_dir=Path('.cache/embeddings'))
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        cache_dir: Path,
    ):
        self.provider = provider
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, list[float]] = {}

        # Load existing cache
        self._load_cache()

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    def _cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        return hashlib.md5(text.encode()).hexdigest()

    def _cache_file(self) -> Path:
        """Get cache file path."""
        return self.cache_dir / "embeddings_cache.json"

    def _load_cache(self) -> None:
        """Load cache from disk."""
        cache_file = self._cache_file()
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    self._cache = json.load(f)
                _debug_log(f"Loaded {len(self._cache)} cached embeddings")
            except Exception as e:
                _debug_log(f"Failed to load embedding cache: {e}")
                self._cache = {}

    def _save_cache(self) -> None:
        """Save cache to disk."""
        try:
            with open(self._cache_file(), 'w') as f:
                json.dump(self._cache, f)
        except Exception as e:
            _debug_log(f"Failed to save embedding cache: {e}")

    def embed_text(self, text: str) -> list[float]:
        """Get embedding, using cache if available."""
        key = self._cache_key(text)

        if key in self._cache:
            return self._cache[key]

        embedding = self.provider.embed_text(text)
        self._cache[key] = embedding
        self._save_cache()

        return embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Get embeddings, using cache where available."""
        results = []
        uncached_texts = []
        uncached_indices = []

        # Check cache
        for i, text in enumerate(texts):
            key = self._cache_key(text)
            if key in self._cache:
                results.append(self._cache[key])
            else:
                results.append(None)
                uncached_texts.append(text)
                uncached_indices.append(i)

        # Compute uncached
        if uncached_texts:
            new_embeddings = self.provider.embed_batch(uncached_texts)

            for idx, text, embedding in zip(
                uncached_indices,
                uncached_texts,
                new_embeddings,
            ):
                key = self._cache_key(text)
                self._cache[key] = embedding
                results[idx] = embedding

            self._save_cache()

        return results


def get_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """
    Factory function to create embedding provider from config.
    
    Args:
        config: Embedding configuration
        
    Returns:
        Configured embedding provider
    """
    if config.provider == "openai":
        provider = OpenAIEmbeddingProvider(
            api_key=config.api_key,
            model=config.model,
        )
    else:
        provider = LocalEmbeddingProvider(
            model_name=config.model,
            cache_dir=config.cache_dir,
        )

    # Wrap with caching if cache_dir specified
    if config.cache_dir:
        provider = CachedEmbeddingProvider(
            provider,
            cache_dir=config.cache_dir / "embeddings",
        )

    return provider
