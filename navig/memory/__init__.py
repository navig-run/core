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
    Message,
    ConversationStore,
    SessionInfo,
)
from navig.memory.embeddings import (
    EmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from navig.memory.knowledge_base import (
    KnowledgeBase,
    KnowledgeEntry,
)
from navig.memory.rag import (
    RAGPipeline,
    RAGConfig,
    RetrievalResult,
)
from navig.memory.user_profile import (
    UserProfile,
    MemoryNote,
    get_profile,
    reload_profile,
)
from navig.memory.storage import (
    MemoryStorage,
    MemoryChunk,
    FileMetadata,
)
from navig.memory.indexer import (
    MemoryIndexer,
    IndexResult,
    ChunkConfig,
)
from navig.memory.search import (
    HybridSearch,
    SearchResult,
    SearchResponse,
    SearchConfig,
)
from navig.memory.manager import (
    MemoryManager,
    get_memory_manager,
    reload_memory_manager,
)
from navig.memory.watcher import (
    MemoryWatcher,
    WatcherContext,
)
from navig.memory.project_indexer import (
    ProjectIndexer,
    ProjectIndexConfig,
    ProjectSearchResult,
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
]
