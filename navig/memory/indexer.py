"""
Memory Indexer - Markdown file scanning and chunking.

Implements semantic structure-aware chunking:
- ~400 tokens per chunk with 80-token overlap
- Respects document structure (headers, paragraphs)
- Maintains line number tracking for citations
- Content-based hashing for change detection
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.memory.embeddings import EmbeddingProvider
    from navig.memory.storage import MemoryChunk, MemoryStorage


from navig.memory._util import _debug_log


@dataclass
class ChunkConfig:
    """Configuration for text chunking."""

    target_tokens: int = 400  # Target tokens per chunk
    overlap_tokens: int = 80  # Overlap between chunks
    min_chunk_tokens: int = 50  # Minimum chunk size
    max_chunk_tokens: int = 600  # Maximum chunk size
    chars_per_token: float = 4.0  # Approximation for token estimation


@dataclass
class IndexResult:
    """Result of indexing operation."""

    files_processed: int = 0
    files_skipped: int = 0  # Unchanged files
    files_failed: int = 0
    chunks_created: int = 0
    chunks_embedded: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class MemoryIndexer:
    """
    Indexes Markdown files into searchable chunks.

    Features:
    - Smart chunking that respects document structure
    - Content hashing for incremental updates
    - Embedding generation with caching
    - Progress tracking

    Usage::

        from navig.platform import paths
        indexer = MemoryIndexer(storage, embedding_provider)
        result = indexer.index_directory(paths.data_dir() / 'memory')
    """

    # File patterns to index
    SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt"}

    # Patterns to skip
    SKIP_PATTERNS = {
        "index.db",
        "index.db-wal",
        "index.db-shm",
        ".git",
        "__pycache__",
        ".DS_Store",
    }

    def __init__(
        self,
        storage: MemoryStorage,
        embedding_provider: EmbeddingProvider | None = None,
        config: ChunkConfig | None = None,
    ):
        self.storage = storage
        self.embedding_provider = embedding_provider
        self.config = config or ChunkConfig()

    def index_directory(
        self,
        directory: Path,
        force_reindex: bool = False,
        embed: bool = True,
        progress_callback: callable | None = None,
    ) -> IndexResult:
        """
        Index all supported files in a directory.

        Args:
            directory: Directory to scan
            force_reindex: Re-index even unchanged files
            embed: Generate embeddings for chunks
            progress_callback: Optional callback(file_path, status)

        Returns:
            IndexResult with statistics
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        start_time = time.time()
        result = IndexResult()

        if not directory.exists():
            result.errors.append(f"Directory not found: {directory}")
            return result

        # Find all supported files
        files = self._find_files(directory)

        chunks_to_embed: list[MemoryChunk] = []
        batch_size = (
            getattr(self.embedding_provider, "batch_size", 32) if self.embedding_provider else 32
        )

        # Phase 1: Pure parsing and chunking. Decoupled from embedding mathematically heavy steps.
        for file_path in files:
            try:
                rel_path = file_path.relative_to(directory).as_posix()

                # Check if file needs reindexing
                file_hash = self._compute_file_hash(file_path)
                if not force_reindex and not self.storage.file_needs_reindex(rel_path, file_hash):
                    result.files_skipped += 1
                    if progress_callback:
                        progress_callback(rel_path, "skipped")
                    continue

                # Index the file (parsing, sqlite storing chunks)
                file_result = self._index_file(
                    file_path, directory, file_hash, embed=False, force_reindex=force_reindex
                )

                if file_result.get("skipped", False):
                    result.files_skipped += 1
                    if progress_callback:
                        progress_callback(rel_path, "skipped")
                    continue

                result.files_processed += 1
                result.chunks_created += file_result["chunks"]
                result.total_tokens += file_result["tokens"]

                # Queue chunks for batch-embedding later
                if embed and self.embedding_provider:
                    chunks_to_embed.extend(file_result.get("chunks_obj", []))

                if progress_callback:
                    progress_callback(rel_path, "indexed")

            except Exception as e:
                result.files_failed += 1
                result.errors.append(f"{file_path}: {str(e)}")
                _debug_log(f"Failed to index {file_path}: {e}")

                if progress_callback:
                    progress_callback(str(file_path), "failed")

        # Phase 2: Parallel Batch Embedding Processing
        if chunks_to_embed and embed and self.embedding_provider:
            batches = [
                chunks_to_embed[i : i + batch_size]
                for i in range(0, len(chunks_to_embed), batch_size)
            ]

            # Bound threads to 4 to prevent out-of-memory or system starvation
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(batches)))) as executor:
                futures = [
                    executor.submit(self._process_embedding_batch, batch) for batch in batches
                ]

                for future in as_completed(futures):
                    try:
                        result.chunks_embedded += future.result()
                    except Exception as e:
                        _debug_log(f"Batch embedding failed: {e}")

        result.duration_seconds = time.time() - start_time
        _debug_log(
            f"Indexed {result.files_processed} files, "
            f"{result.chunks_created} chunks, "
            f"{result.chunks_embedded} embeddings in {result.duration_seconds:.2f}s"
        )

        return result

    def _process_embedding_batch(self, chunks: list[MemoryChunk]) -> int:
        """Process a batch of chunks for embedding."""
        if not chunks or not self.embedding_provider:
            return 0

        from navig.memory.storage import MemoryStorage

        model_name = getattr(self.embedding_provider, "model_name", "unknown")

        # 1. Check cache first
        to_embed = []
        updates = []
        embedded_count = 0

        for chunk in chunks:
            content_hash = MemoryStorage.compute_content_hash(chunk.content)
            cached = self.storage.get_cached_embedding(content_hash, model_name)

            if cached:
                chunk.embedding = cached
                updates.append((chunk.id, cached))
                embedded_count += 1
            else:
                to_embed.append((chunk, content_hash))

        # 2. Update chunks found in cache
        if updates:
            self.storage.update_chunk_embeddings(updates)

        # 3. Generate new embeddings
        if to_embed:
            texts = [chunk.content for chunk, _ in to_embed]
            try:
                embeddings = self.embedding_provider.embed_batch(texts)

                new_updates = []
                cache_entries = []

                for (chunk, content_hash), embedding in zip(to_embed, embeddings):
                    chunk.embedding = embedding
                    new_updates.append((chunk.id, embedding))
                    cache_entries.append((content_hash, embedding, model_name))
                    embedded_count += 1

                # Batch update storage
                if new_updates:
                    self.storage.update_chunk_embeddings(new_updates)
                if cache_entries:
                    self.storage.upsert_embedding_cache(cache_entries)

            except Exception as e:
                _debug_log(f"Failed to embed batch: {e}")

        return embedded_count

    def index_file(
        self,
        file_path: Path,
        base_directory: Path | None = None,
        embed: bool = True,
        force_reindex: bool = False,
    ) -> IndexResult:
        """
        Index a single file.

        Args:
            file_path: Path to the file to index.
            base_directory: Root used for relative path storage.  Defaults to
                the file's parent directory.
            embed: Generate embeddings for the resulting chunks.
            force_reindex: Re-index even if the file hash is unchanged.
        """
        import time

        start_time = time.time()
        result = IndexResult()

        if not file_path.exists():
            result.errors.append(f"File not found: {file_path}")
            result.files_failed += 1
            return result

        base_dir = base_directory or file_path.parent
        file_hash = self._compute_file_hash(file_path)

        try:
            # Index without embedding first
            file_result = self._index_file(
                file_path, base_dir, file_hash, embed=False, force_reindex=force_reindex
            )
            if file_result.get("skipped", False):
                # Unchanged file — nothing to do
                result.files_skipped = 1
                result.duration_seconds = time.time() - start_time
                return result
            result.files_processed = 1
            result.chunks_created = file_result["chunks"]
            result.total_tokens = file_result["tokens"]

            # Embed if requested
            if embed and self.embedding_provider:
                chunks = file_result.get("chunks_obj", [])
                if chunks:
                    result.chunks_embedded = self._process_embedding_batch(chunks)

        except Exception as e:
            result.files_failed = 1
            result.errors.append(str(e))
            _debug_log(f"Failed to index file {file_path}: {e}")

        result.duration_seconds = time.time() - start_time
        return result

    def _index_file(
        self,
        file_path: Path,
        base_directory: Path,
        file_hash: str,
        embed: bool = True,
        force_reindex: bool = False,
    ) -> dict:
        """Internal file indexing implementation.

        When *force_reindex* is ``False`` (the default) the method returns a
        ``{"skipped": True}`` dict if the storage already holds an up-to-date
        record for this file hash, making every call site idempotent.
        """
        from navig.memory.storage import FileMetadata

        rel_path = file_path.relative_to(base_directory).as_posix()

        # Guard: skip if unchanged and not forced
        if not force_reindex and not self.storage.file_needs_reindex(rel_path, file_hash):
            return {"chunks": 0, "tokens": 0, "embedded": 0, "chunks_obj": [], "skipped": True}

        # Read file content
        content = file_path.read_text(encoding="utf-8", errors="replace")

        # Delete existing chunks for this file
        self.storage.delete_chunks_for_file(rel_path)

        # Chunk the content
        chunks = list(self._chunk_text(content, rel_path))

        # Store chunks (without embeddings initially)
        self.storage.upsert_chunks(chunks)

        # Update file metadata
        total_tokens = sum(c.token_count for c in chunks)
        metadata = FileMetadata(
            file_path=rel_path,
            file_hash=file_hash,
            last_modified=datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
            chunk_count=len(chunks),
            total_tokens=total_tokens,
        )
        self.storage.upsert_file_metadata(metadata)

        # If embed=True (legacy/direct call), process immediately
        embedded_count = 0
        if embed and self.embedding_provider and chunks:
            embedded_count = self._process_embedding_batch(chunks)

        return {
            "chunks": len(chunks),
            "tokens": total_tokens,
            "embedded": embedded_count,
            "chunks_obj": chunks,  # Return objects for batch processing
            "skipped": False,
        }

    def _chunk_text(
        self,
        text: str,
        file_path: str,
    ) -> Generator[MemoryChunk, None, None]:
        """
        Chunk text with overlap while respecting structure.

        Chunking strategy:
        1. Split into blocks (headers, paragraphs, code blocks)
        2. Merge small blocks, split large blocks
        3. Add overlap between chunks
        """

        lines = text.split("\n")
        blocks = self._extract_blocks(lines)

        current_chunk_lines = []
        current_chunk_tokens = 0
        current_start_line = 1
        overlap_buffer = []

        for _block_start, _block_end, block_text in blocks:
            block_tokens = self._estimate_tokens(block_text)

            # If adding this block would exceed max, flush current chunk
            if (
                current_chunk_tokens + block_tokens > self.config.max_chunk_tokens
                and current_chunk_lines
            ):
                # Create chunk from current content
                chunk_content = "\n".join(current_chunk_lines)
                chunk_end_line = current_start_line + len(current_chunk_lines) - 1

                yield self._create_chunk(
                    content=chunk_content,
                    file_path=file_path,
                    line_start=current_start_line,
                    line_end=chunk_end_line,
                )

                # Keep overlap for next chunk
                overlap_tokens = 0
                overlap_buffer = []
                for line in reversed(current_chunk_lines):
                    line_tokens = self._estimate_tokens(line)
                    if overlap_tokens + line_tokens > self.config.overlap_tokens:
                        break
                    overlap_buffer.insert(0, line)
                    overlap_tokens += line_tokens

                # Start new chunk with overlap
                current_chunk_lines = overlap_buffer.copy()
                current_chunk_tokens = overlap_tokens
                current_start_line = chunk_end_line - len(overlap_buffer) + 1

            # Add block to current chunk
            block_lines = block_text.split("\n")
            current_chunk_lines.extend(block_lines)
            current_chunk_tokens += block_tokens

        # Flush remaining content
        if current_chunk_lines and current_chunk_tokens >= self.config.min_chunk_tokens:
            chunk_content = "\n".join(current_chunk_lines)
            chunk_end_line = current_start_line + len(current_chunk_lines) - 1

            yield self._create_chunk(
                content=chunk_content,
                file_path=file_path,
                line_start=current_start_line,
                line_end=chunk_end_line,
            )

    def _extract_blocks(
        self,
        lines: list[str],
    ) -> list[tuple[int, int, str]]:
        """
        Extract logical blocks from lines.

        Returns list of (start_line, end_line, text) tuples.
        """
        blocks = []
        current_block_lines = []
        current_start = 1
        in_code_block = False

        for i, line in enumerate(lines, 1):
            # Track code blocks
            if line.strip().startswith("```"):
                if in_code_block:
                    # End of code block
                    current_block_lines.append(line)
                    blocks.append((current_start, i, "\n".join(current_block_lines)))
                    current_block_lines = []
                    current_start = i + 1
                    in_code_block = False
                else:
                    # Start of code block - flush current
                    if current_block_lines:
                        blocks.append((current_start, i - 1, "\n".join(current_block_lines)))
                    current_block_lines = [line]
                    current_start = i
                    in_code_block = True
                continue

            if in_code_block:
                current_block_lines.append(line)
                continue

            # Headers start new blocks
            if line.startswith("#"):
                if current_block_lines:
                    blocks.append((current_start, i - 1, "\n".join(current_block_lines)))
                current_block_lines = [line]
                current_start = i
                continue

            # Empty lines between paragraphs
            if not line.strip():
                if current_block_lines and current_block_lines[-1].strip():
                    # Keep the empty line as part of the block
                    current_block_lines.append(line)
                continue

            current_block_lines.append(line)

        # Flush remaining
        if current_block_lines:
            blocks.append((current_start, len(lines), "\n".join(current_block_lines)))

        return blocks

    def _create_chunk(
        self,
        content: str,
        file_path: str,
        line_start: int,
        line_end: int,
    ) -> MemoryChunk:
        """Create a MemoryChunk instance."""
        from navig.memory.storage import MemoryChunk, MemoryStorage

        content_hash = MemoryStorage.compute_content_hash(content)
        token_count = self._estimate_tokens(content)

        return MemoryChunk(
            id=f"{file_path}:{line_start}-{line_end}:{content_hash}",
            file_path=file_path,
            content=content,
            line_start=line_start,
            line_end=line_end,
            token_count=token_count,
        )

    def _embed_chunks(self, chunks: list[MemoryChunk]) -> int:
        """Generate embeddings for chunks using cache."""
        if not self.embedding_provider:
            return 0

        from navig.memory.storage import MemoryStorage

        model_name = getattr(self.embedding_provider, "model_name", "unknown")
        embedded = 0

        # Check cache first
        to_embed = []
        for chunk in chunks:
            content_hash = MemoryStorage.compute_content_hash(chunk.content)
            cached = self.storage.get_cached_embedding(content_hash, model_name)

            if cached:
                chunk.embedding = cached
                embedded += 1
            else:
                to_embed.append((chunk, content_hash))

        # Batch embed new chunks
        if to_embed:
            texts = [chunk.content for chunk, _ in to_embed]
            embeddings = self.embedding_provider.embed_batch(texts)

            for (chunk, content_hash), embedding in zip(to_embed, embeddings):
                chunk.embedding = embedding
                # Cache the embedding
                self.storage.cache_embedding(content_hash, embedding, model_name)
                embedded += 1

        return embedded

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from text."""
        from navig.core.tokens import estimate_tokens

        return estimate_tokens(text, chars_per_token=self.config.chars_per_token)

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file content."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()[:16]

    def _find_files(self, directory: Path) -> list[Path]:
        """Find all indexable files in directory."""
        files = []

        for path in directory.rglob("*"):
            # Skip directories and hidden files
            if path.is_dir():
                continue

            # Skip by pattern
            if any(skip in path.parts for skip in self.SKIP_PATTERNS):
                continue

            # Check extension
            if path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                files.append(path)

        return sorted(files)

    def remove_deleted_files(self, directory: Path) -> int:
        """Remove index entries for files that no longer exist."""
        removed = 0

        for file_meta in self.storage.get_all_files():
            file_path = directory / file_meta.file_path
            if not file_path.exists():
                self.storage.delete_file(file_meta.file_path)
                removed += 1
                _debug_log(f"Removed deleted file from index: {file_meta.file_path}")

        return removed
