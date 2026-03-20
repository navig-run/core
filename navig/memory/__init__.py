"""
NAVIG Memory Module - Context and conversation management.

Implements advanced memory patterns:
- Vector embeddings and semantic search (embeddings.py)
- Content chunking and indexing (indexer.py)
- Context retrieval and history management
- SQLite-backed storage with FTS5 optimization

Reference: docs/concepts/memory.md
"""

from navig.memory.conversation import (
    ConversationStore,
    Message,
    SessionInfo,
)
from navig.memory.embeddings import (
    EmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from navig.memory.fact_extractor import (
    ExtractionResult,
    FactExtractor,
    extract_rules,
)
from navig.memory.fact_retriever import (
    FactRetrievalResult,
    FactRetriever,
    RankedFact,
    RetrievalConfig,
)
from navig.memory.indexer import (
    ChunkConfig,
    IndexResult,
    MemoryIndexer,
)
from navig.memory.key_facts import (
    VALID_CATEGORIES,
    KeyFact,
    KeyFactStore,
    get_key_fact_store,
    reset_key_fact_store,
)
from navig.memory.knowledge_base import (
    KnowledgeBase,
    KnowledgeEntry,
)
from navig.memory.manager import (
    MemoryManager,
    get_memory_manager,
    reload_memory_manager,
)
from navig.memory.project_indexer import (
    ProjectIndexConfig,
    ProjectIndexer,
    ProjectSearchResult,
)
from navig.memory.rag import (
    RAGConfig,
    RAGPipeline,
    RetrievalResult,
)
from navig.memory.search import (
    HybridSearch,
    SearchConfig,
    SearchResponse,
    SearchResult,
)
from navig.memory.storage import (
    FileMetadata,
    MemoryChunk,
    MemoryStorage,
)
from navig.memory.user_profile import (
    MemoryNote,
    UserProfile,
    get_profile,
    reload_profile,
)
from navig.memory.watcher import (
    MemoryWatcher,
    WatcherContext,
)

__all__ = [
    # Conversation
    'Message',
    'ConversationStore',
    'SessionInfo',
    # Embeddings
    'EmbeddingProvider',
    'LocalEmbeddingProvider',
    'OpenAIEmbeddingProvider',
    # Knowledge Base
    'KnowledgeBase',
    'KnowledgeEntry',
    # RAG
    'RAGPipeline',
    'RAGConfig',
    'RetrievalResult',
    # User Profile
    'UserProfile',
    'MemoryNote',
    'get_profile',
    'reload_profile',
    # Memory Bank Storage
    'MemoryStorage',
    'MemoryChunk',
    'FileMetadata',
    # Memory Indexer
    'MemoryIndexer',
    'IndexResult',
    'ChunkConfig',
    # Project Indexer
    'ProjectIndexer',
    'ProjectIndexConfig',
    'ProjectSearchResult',
    # Hybrid Search
    'HybridSearch',
    'SearchResult',
    'SearchResponse',
    'SearchConfig',
    # Memory Manager
    'MemoryManager',
    'get_memory_manager',
    'reload_memory_manager',
    # File Watcher
    'MemoryWatcher',
    'WatcherContext',
    # Key Facts (Conversational Memory)
    'KeyFact',
    'KeyFactStore',
    'get_key_fact_store',
    'reset_key_fact_store',
    'VALID_CATEGORIES',
    # Fact Extraction
    'FactExtractor',
    'ExtractionResult',
    'extract_rules',
    # Fact Retrieval
    'FactRetriever',
    'FactRetrievalResult',
    'RankedFact',
    'RetrievalConfig',
]
