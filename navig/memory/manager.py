"""
Memory Manager - Main orchestrator for memory bank operations.

Provides a unified interface to:
- Index files into the memory bank
- Search with hybrid (vector + keyword) queries
- Inject context into AI prompts
- Manage memory lifecycle
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from navig.memory.storage import MemoryStorage
    from navig.memory.indexer import MemoryIndexer, IndexResult
    from navig.memory.search import HybridSearch, SearchResponse, SearchResult
    from navig.memory.embeddings import EmbeddingProvider


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger
        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:
        pass


def _get_memory_dir() -> Path:
    """Get the default memory directory."""
    return Path.home() / '.navig' / 'memory'


# Module-level singleton
_manager_instance: Optional['MemoryManager'] = None


def get_memory_manager(
    memory_dir: Optional[Path] = None,
    use_embeddings: bool = True,
) -> 'MemoryManager':
    """
    Get or create the singleton memory manager.
    
    Args:
        memory_dir: Optional custom memory directory
        use_embeddings: Whether to enable vector embeddings
        
    Returns:
        MemoryManager instance
    """
    global _manager_instance
    
    if _manager_instance is None:
        _manager_instance = MemoryManager(
            memory_dir=memory_dir or _get_memory_dir(),
            use_embeddings=use_embeddings,
        )
    
    return _manager_instance


def reload_memory_manager() -> 'MemoryManager':
    """Force reload of the memory manager."""
    global _manager_instance
    
    if _manager_instance:
        _manager_instance.close()
    
    _manager_instance = None
    return get_memory_manager()


class MemoryManager:
    """
    Unified interface for memory bank operations.
    
    Coordinates storage, indexing, and search components.
    
    Usage:
        manager = MemoryManager()
        
        # Index all files
        result = manager.index()
        
        # Search
        response = manager.search("docker networking")
        
        # Get context for AI
        context = manager.get_context("How do I configure nginx?")
    """
    
    def __init__(
        self,
        memory_dir: Optional[Path] = None,
        use_embeddings: bool = True,
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        """
        Initialize memory manager.
        
        Args:
            memory_dir: Directory containing memory files
            use_embeddings: Enable vector embeddings
            embedding_model: Sentence transformer model to use
        """
        self.memory_dir = memory_dir or _get_memory_dir()
        self.use_embeddings = use_embeddings
        self.embedding_model = embedding_model
        
        # Ensure directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components lazily
        self._storage: Optional['MemoryStorage'] = None
        self._embedding_provider: Optional['EmbeddingProvider'] = None
        self._indexer: Optional['MemoryIndexer'] = None
        self._search: Optional['HybridSearch'] = None
        
        _debug_log(f"MemoryManager initialized: {self.memory_dir}")
    
    @property
    def storage(self) -> 'MemoryStorage':
        """Get or create storage instance."""
        if self._storage is None:
            from navig.memory.storage import MemoryStorage
            
            db_path = self.memory_dir / 'index.db'
            self._storage = MemoryStorage(db_path)
        
        return self._storage
    
    @property
    def embedding_provider(self) -> Optional['EmbeddingProvider']:
        """Get or create embedding provider."""
        if self._embedding_provider is None and self.use_embeddings:
            try:
                from navig.memory.embeddings import LocalEmbeddingProvider
                
                self._embedding_provider = LocalEmbeddingProvider(
                    model_name=self.embedding_model
                )
                _debug_log(f"Loaded embedding model: {self.embedding_model}")
            except ImportError as e:
                _debug_log(f"Embeddings unavailable: {e}")
                self.use_embeddings = False
        
        return self._embedding_provider
    
    @property
    def indexer(self) -> 'MemoryIndexer':
        """Get or create indexer instance."""
        if self._indexer is None:
            from navig.memory.indexer import MemoryIndexer
            
            self._indexer = MemoryIndexer(
                storage=self.storage,
                embedding_provider=self.embedding_provider,
            )
        
        return self._indexer
    
    @property
    def search_engine(self) -> 'HybridSearch':
        """Get or create search engine."""
        if self._search is None:
            from navig.memory.search import HybridSearch
            
            self._search = HybridSearch(
                storage=self.storage,
                embedding_provider=self.embedding_provider,
            )
        
        return self._search
    
    # ---------- Indexing Operations ----------
    
    def index(
        self,
        force: bool = False,
        embed: bool = True,
        progress_callback: Optional[callable] = None,
    ) -> 'IndexResult':
        """
        Index all files in the memory directory.
        
        Args:
            force: Re-index even unchanged files
            embed: Generate embeddings
            progress_callback: Optional callback(file_path, status)
            
        Returns:
            IndexResult with statistics
        """
        result = self.indexer.index_directory(
            self.memory_dir,
            force_reindex=force,
            embed=embed and self.use_embeddings,
            progress_callback=progress_callback,
        )
        
        # Clean up deleted files
        removed = self.indexer.remove_deleted_files(self.memory_dir)
        if removed:
            _debug_log(f"Removed {removed} deleted files from index")
        
        return result
    
    def index_file(
        self,
        file_path: Path,
        embed: bool = True,
    ) -> 'IndexResult':
        """
        Index a single file.
        
        Args:
            file_path: Path to the file
            embed: Generate embeddings
            
        Returns:
            IndexResult for this file
        """
        return self.indexer.index_file(
            file_path,
            base_directory=self.memory_dir,
            embed=embed and self.use_embeddings,
        )
    
    # ---------- Search Operations ----------
    
    def search(
        self,
        query: str,
        limit: int = 10,
        file_filter: Optional[str] = None,
    ) -> 'SearchResponse':
        """
        Search the memory bank.
        
        Args:
            query: Search query
            limit: Maximum results
            file_filter: Optional file path pattern
            
        Returns:
            SearchResponse with results
        """
        return self.search_engine.search(
            query=query,
            limit=limit,
            file_filter=file_filter,
        )
    
    def search_similar(
        self,
        chunk_id: str,
        limit: int = 5,
    ) -> 'SearchResponse':
        """
        Find chunks similar to a given chunk.
        
        Args:
            chunk_id: Source chunk ID
            limit: Maximum results
            
        Returns:
            SearchResponse with similar chunks
        """
        return self.search_engine.search_similar(chunk_id, limit)
    
    # ---------- Context Injection ----------
    
    def get_context(
        self,
        query: str,
        max_tokens: int = 2000,
        limit: int = 5,
    ) -> str:
        """
        Get formatted context for AI prompts.
        
        Args:
            query: Query to search for
            max_tokens: Approximate token limit
            limit: Maximum results to include
            
        Returns:
            Formatted context string with citations
        """
        response = self.search(query, limit=limit)
        
        if not response.results:
            return ""
        
        return response.as_context(max_tokens=max_tokens)
    
    def get_context_with_sources(
        self,
        query: str,
        max_tokens: int = 2000,
        limit: int = 5,
    ) -> tuple[str, List['SearchResult']]:
        """
        Get context and source list for AI prompts.
        
        Args:
            query: Query to search for
            max_tokens: Approximate token limit
            limit: Maximum results
            
        Returns:
            Tuple of (context_string, list_of_results)
        """
        response = self.search(query, limit=limit)
        context = response.as_context(max_tokens=max_tokens)
        
        return context, response.results
    
    # ---------- File Management ----------
    
    def add_file(
        self,
        content: str,
        filename: str,
        subdirectory: str = "",
    ) -> Path:
        """
        Add a new file to the memory directory.
        
        Args:
            content: File content
            filename: Filename (e.g., "project-notes.md")
            subdirectory: Optional subdirectory
            
        Returns:
            Path to the created file
        """
        if subdirectory:
            target_dir = self.memory_dir / subdirectory
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            target_dir = self.memory_dir
        
        file_path = target_dir / filename
        file_path.write_text(content, encoding='utf-8')
        
        # Index the new file
        self.index_file(file_path)
        
        return file_path
    
    def remove_file(self, file_path: str) -> bool:
        """
        Remove a file from the memory bank.
        
        Args:
            file_path: Relative path from memory directory
            
        Returns:
            True if file was removed
        """
        full_path = self.memory_dir / file_path
        
        if full_path.exists():
            full_path.unlink()
        
        # Remove from index
        deleted = self.storage.delete_file(file_path)
        return deleted > 0
    
    def list_files(self) -> List[dict]:
        """
        List all indexed files.
        
        Returns:
            List of file metadata dicts
        """
        files = self.storage.get_all_files()
        return [f.to_dict() for f in files]
    
    # ---------- Statistics ----------
    
    def get_stats(self) -> dict:
        """
        Get memory bank statistics.
        
        Returns:
            Dict with storage stats
        """
        stats = self.storage.get_stats()
        
        # Add additional info
        stats['memory_dir'] = str(self.memory_dir)
        stats['embeddings_enabled'] = self.use_embeddings
        if self.use_embeddings:
            stats['embedding_model'] = self.embedding_model
        
        return stats
    
    def get_status(self) -> dict:
        """
        Get detailed status for CLI display.
        
        Returns:
            Dict with status information
        """
        stats = self.get_stats()
        
        return {
            'memory_directory': str(self.memory_dir),
            'indexed_files': stats['file_count'],
            'total_chunks': stats['chunk_count'],
            'total_tokens': stats['total_tokens'],
            'embedded_chunks': stats['embedded_chunks'],
            'embedding_cache': stats['embedding_cache_size'],
            'database_size_mb': stats['database_size_mb'],
            'embeddings_enabled': self.use_embeddings,
            'embedding_model': self.embedding_model if self.use_embeddings else None,
        }
    
    # ---------- Maintenance ----------
    
    def clear(self, confirm: bool = False) -> dict:
        """
        Clear all indexed data.
        
        Args:
            confirm: Must be True to proceed
            
        Returns:
            Dict with deletion counts
        """
        if not confirm:
            raise ValueError("Must set confirm=True to clear memory index")
        
        result = self.storage.clear_all()
        _debug_log(f"Cleared memory index: {result}")
        return result
    
    def vacuum(self) -> None:
        """Compact the database."""
        self.storage.vacuum()
        _debug_log("Database vacuumed")
    
    def close(self) -> None:
        """Close all connections."""
        if self._storage:
            self._storage.close()
            self._storage = None
        
        self._embedding_provider = None
        self._indexer = None
        self._search = None
    
    # ---------- Tools for AI Agent ----------
    
    def memory_search_tool(self, query: str) -> str:
        """
        Tool function for AI agent to search memory.
        
        Args:
            query: Natural language query
            
        Returns:
            Formatted results string
        """
        response = self.search(query, limit=5)
        
        if not response.results:
            return "No relevant memory found."
        
        lines = [f"Found {len(response.results)} relevant memory entries:\n"]
        
        for i, result in enumerate(response.results, 1):
            lines.append(f"{i}. {result.citation()}")
            lines.append(f"   Score: {result.combined_score:.2f}")
            lines.append(f"   {result.snippet}")
            lines.append("")
        
        return "\n".join(lines)
    
    def memory_get_tool(self, file_path: str) -> str:
        """
        Tool function for AI agent to get file content.
        
        Args:
            file_path: Relative path to file
            
        Returns:
            File content or error message
        """
        full_path = self.memory_dir / file_path
        
        if not full_path.exists():
            return f"File not found: {file_path}"
        
        try:
            content = full_path.read_text(encoding='utf-8')
            return f"# {file_path}\n\n{content}"
        except Exception as e:
            return f"Error reading {file_path}: {e}"
