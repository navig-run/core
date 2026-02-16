"""
Hybrid Search Engine - Vector + BM25 keyword search.

Implements advanced hybrid search:
- 70% vector similarity (semantic understanding)
- 30% BM25 keyword match (exact term matching)
- Configurable weights and filters
- Citation-ready result format
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from navig.memory.storage import MemoryStorage, MemoryChunk
    from navig.memory.embeddings import EmbeddingProvider


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger
        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:
        pass


@dataclass
class SearchConfig:
    """Configuration for hybrid search."""
    
    # Weight distribution
    vector_weight: float = 0.7  # Weight for vector similarity
    keyword_weight: float = 0.3  # Weight for BM25 keyword match
    
    # Search parameters
    default_limit: int = 10
    min_score: float = 0.1  # Minimum combined score to include
    
    # Vector search
    min_vector_similarity: float = 0.3
    
    # Keyword search
    enable_stemming: bool = True  # Basic word stemming
    
    # Result formatting
    snippet_length: int = 200  # Characters for context snippets
    highlight_matches: bool = True


@dataclass
class SearchResult:
    """A single search result with scoring and metadata."""
    
    chunk_id: str
    file_path: str
    content: str
    line_start: int
    line_end: int
    
    # Scoring
    combined_score: float
    vector_score: float = 0.0
    keyword_score: float = 0.0
    
    # Context
    snippet: str = ""
    highlights: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            'chunk_id': self.chunk_id,
            'file_path': self.file_path,
            'content': self.content,
            'line_start': self.line_start,
            'line_end': self.line_end,
            'combined_score': round(self.combined_score, 4),
            'vector_score': round(self.vector_score, 4),
            'keyword_score': round(self.keyword_score, 4),
            'snippet': self.snippet,
            'highlights': self.highlights,
        }
    
    def citation(self) -> str:
        """Generate citation string for this result."""
        return f"[source: {self.file_path}:{self.line_start}-{self.line_end}]"
    
    def short_citation(self) -> str:
        """Generate short citation."""
        return f"{self.file_path}:{self.line_start}"


@dataclass
class SearchResponse:
    """Complete search response with results and metadata."""
    
    query: str
    results: List[SearchResult]
    total_matches: int = 0
    search_time_ms: float = 0.0
    
    # Source breakdown
    vector_candidates: int = 0
    keyword_candidates: int = 0
    
    def to_dict(self) -> dict:
        return {
            'query': self.query,
            'results': [r.to_dict() for r in self.results],
            'total_matches': self.total_matches,
            'search_time_ms': round(self.search_time_ms, 2),
            'vector_candidates': self.vector_candidates,
            'keyword_candidates': self.keyword_candidates,
        }
    
    def as_context(self, max_tokens: int = 2000) -> str:
        """
        Format results as context for AI prompts.
        
        Args:
            max_tokens: Approximate token limit
            
        Returns:
            Formatted context string with citations
        """
        if not self.results:
            return ""
        
        parts = ["## Relevant Memory Context\n"]
        approx_tokens = 10  # Header overhead
        chars_per_token = 4
        
        for result in self.results:
            # Estimate tokens for this result
            result_tokens = len(result.content) // chars_per_token + 10
            
            if approx_tokens + result_tokens > max_tokens:
                break
            
            parts.append(f"### {result.citation()}")
            parts.append(result.content)
            parts.append("")
            
            approx_tokens += result_tokens
        
        return "\n".join(parts)


class HybridSearch:
    """
    Hybrid search combining vector similarity and BM25 keyword matching.
    
    The combination formula:
        score = (vector_weight * vector_score) + (keyword_weight * keyword_score)
    
    Default weights: 70% vector, 30% keyword (following best practices)
    
    Usage:
        search = HybridSearch(storage, embedding_provider)
        response = search.search("docker compose networking")
        
        # Get formatted context
        context = response.as_context(max_tokens=2000)
    """
    
    def __init__(
        self,
        storage: 'MemoryStorage',
        embedding_provider: Optional['EmbeddingProvider'] = None,
        config: Optional[SearchConfig] = None,
    ):
        self.storage = storage
        self.embedding_provider = embedding_provider
        self.config = config or SearchConfig()
    
    def search(
        self,
        query: str,
        limit: Optional[int] = None,
        file_filter: Optional[str] = None,
        vector_only: bool = False,
        keyword_only: bool = False,
    ) -> SearchResponse:
        """
        Perform hybrid search.
        
        Args:
            query: Search query
            limit: Maximum results
            file_filter: Optional file path pattern (supports wildcards)
            vector_only: Only use vector search
            keyword_only: Only use keyword search
            
        Returns:
            SearchResponse with ranked results
        """
        import time
        start_time = time.time()
        
        limit = limit or self.config.default_limit
        
        # Get results from both methods
        vector_results: Dict[str, float] = {}
        keyword_results: Dict[str, float] = {}
        chunks: Dict[str, 'MemoryChunk'] = {}
        
        # Vector search
        if not keyword_only and self.embedding_provider:
            vector_matches = self._vector_search(query, limit * 2, file_filter)
            for chunk, score in vector_matches:
                vector_results[chunk.id] = score
                chunks[chunk.id] = chunk
        
        # Keyword search
        if not vector_only:
            keyword_matches = self._keyword_search(query, limit * 2, file_filter)
            for chunk, score in keyword_matches:
                keyword_results[chunk.id] = score
                if chunk.id not in chunks:
                    chunks[chunk.id] = chunk
        
        # Combine and rank results
        combined_scores: Dict[str, Dict[str, float]] = {}
        all_chunk_ids = set(vector_results.keys()) | set(keyword_results.keys())
        
        # Find max keyword score for normalization
        max_keyword_score = max(keyword_results.values()) if keyword_results else 1.0
        if max_keyword_score <= 0:
            max_keyword_score = 1.0
        
        for chunk_id in all_chunk_ids:
            v_score = vector_results.get(chunk_id, 0.0)
            k_score = keyword_results.get(chunk_id, 0.0)
            
            # Normalize keyword score relative to best match
            # If any keyword match exists, give it a reasonable score
            if k_score > 0:
                k_score_normalized = max(0.3, min(1.0, k_score / max_keyword_score))
            else:
                k_score_normalized = 0.0
            
            # Compute combined score
            if vector_only:
                combined = v_score
            elif keyword_only:
                combined = k_score_normalized
            else:
                combined = (
                    self.config.vector_weight * v_score +
                    self.config.keyword_weight * k_score_normalized
                )
            
            combined_scores[chunk_id] = {
                'combined': combined,
                'vector': v_score,
                'keyword': k_score_normalized,
            }
        
        # Sort by combined score and filter
        sorted_ids = sorted(
            combined_scores.keys(),
            key=lambda x: combined_scores[x]['combined'],
            reverse=True
        )
        
        # Build results
        results = []
        for chunk_id in sorted_ids[:limit]:
            scores = combined_scores[chunk_id]
            
            if scores['combined'] < self.config.min_score:
                continue
            
            chunk = chunks[chunk_id]
            
            # Generate snippet and highlights
            snippet = self._generate_snippet(chunk.content, query)
            highlights = self._extract_highlights(chunk.content, query)
            
            result = SearchResult(
                chunk_id=chunk.id,
                file_path=chunk.file_path,
                content=chunk.content,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                combined_score=scores['combined'],
                vector_score=scores['vector'],
                keyword_score=scores['keyword'],
                snippet=snippet,
                highlights=highlights,
            )
            results.append(result)
        
        search_time = (time.time() - start_time) * 1000
        
        response = SearchResponse(
            query=query,
            results=results,
            total_matches=len(results),
            search_time_ms=search_time,
            vector_candidates=len(vector_results),
            keyword_candidates=len(keyword_results),
        )
        
        _debug_log(
            f"Search '{query[:50]}': {len(results)} results "
            f"(vector: {len(vector_results)}, keyword: {len(keyword_results)}) "
            f"in {search_time:.1f}ms"
        )
        
        return response
    
    def _vector_search(
        self,
        query: str,
        limit: int,
        file_filter: Optional[str] = None,
    ) -> List[tuple['MemoryChunk', float]]:
        """Perform vector similarity search."""
        if not self.embedding_provider:
            return []
        
        # Get query embedding
        query_embedding = self.embedding_provider.embed_text(query)
        
        # Get all chunks with embeddings
        chunks = self.storage.get_all_chunks_with_embeddings()
        
        # Apply file filter if specified
        if file_filter:
            pattern = file_filter.replace('*', '.*')
            chunks = [c for c in chunks if re.match(pattern, c.file_path)]
        
        # Compute similarities
        results = []
        for chunk in chunks:
            if chunk.embedding:
                similarity = self.embedding_provider.similarity(
                    query_embedding,
                    chunk.embedding
                )
                
                if similarity >= self.config.min_vector_similarity:
                    results.append((chunk, similarity))
        
        # Sort by similarity
        results.sort(key=lambda x: x[1], reverse=True)
        
        return results[:limit]
    
    def _keyword_search(
        self,
        query: str,
        limit: int,
        file_filter: Optional[str] = None,
    ) -> List[tuple['MemoryChunk', float]]:
        """Perform BM25 keyword search."""
        return self.storage.search_fts_simple(query, limit)
    
    def _generate_snippet(self, content: str, query: str) -> str:
        """Generate a relevant snippet from content."""
        length = self.config.snippet_length
        
        # Try to find query terms in content
        query_words = query.lower().split()
        content_lower = content.lower()
        
        best_pos = 0
        for word in query_words:
            pos = content_lower.find(word)
            if pos != -1:
                best_pos = max(0, pos - length // 4)
                break
        
        # Extract snippet around best position
        start = best_pos
        end = min(len(content), start + length)
        
        snippet = content[start:end]
        
        # Clean up snippet
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        
        return snippet.replace('\n', ' ').strip()
    
    def _extract_highlights(self, content: str, query: str) -> List[str]:
        """Extract matching phrases from content."""
        highlights = []
        query_words = set(query.lower().split())
        
        # Find sentences containing query words
        sentences = re.split(r'[.!?\n]+', content)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            sentence_words = set(sentence.lower().split())
            matching_words = query_words & sentence_words
            
            if matching_words:
                # Truncate long sentences
                if len(sentence) > 100:
                    sentence = sentence[:100] + "..."
                highlights.append(sentence)
                
                if len(highlights) >= 3:
                    break
        
        return highlights
    
    def search_similar(
        self,
        chunk_id: str,
        limit: int = 5,
    ) -> SearchResponse:
        """
        Find chunks similar to a given chunk.
        
        Args:
            chunk_id: ID of the source chunk
            limit: Maximum results
            
        Returns:
            SearchResponse with similar chunks
        """
        import time
        start_time = time.time()
        
        # Get the source chunk
        source_chunk = self.storage.get_chunk(chunk_id)
        if not source_chunk or not source_chunk.embedding:
            return SearchResponse(
                query=f"similar:{chunk_id}",
                results=[],
            )
        
        # Find similar chunks
        if not self.embedding_provider:
            return SearchResponse(
                query=f"similar:{chunk_id}",
                results=[],
            )
        
        chunks = self.storage.get_all_chunks_with_embeddings()
        
        results = []
        for chunk in chunks:
            if chunk.id == chunk_id:
                continue
            
            if chunk.embedding:
                similarity = self.embedding_provider.similarity(
                    source_chunk.embedding,
                    chunk.embedding
                )
                
                if similarity >= self.config.min_vector_similarity:
                    result = SearchResult(
                        chunk_id=chunk.id,
                        file_path=chunk.file_path,
                        content=chunk.content,
                        line_start=chunk.line_start,
                        line_end=chunk.line_end,
                        combined_score=similarity,
                        vector_score=similarity,
                    )
                    results.append(result)
        
        # Sort and limit
        results.sort(key=lambda x: x.combined_score, reverse=True)
        results = results[:limit]
        
        search_time = (time.time() - start_time) * 1000
        
        return SearchResponse(
            query=f"similar:{chunk_id}",
            results=results,
            total_matches=len(results),
            search_time_ms=search_time,
            vector_candidates=len(chunks),
        )
    
    def suggest_queries(self, partial: str, limit: int = 5) -> List[str]:
        """
        Suggest query completions based on indexed content.
        
        Args:
            partial: Partial query string
            limit: Maximum suggestions
            
        Returns:
            List of suggested queries
        """
        if len(partial) < 2:
            return []
        
        # Search for matching terms
        results = self.storage.search_fts_simple(f'"{partial}"*', limit * 2)
        
        # Extract unique phrases containing the partial
        suggestions = set()
        partial_lower = partial.lower()
        
        for chunk, _ in results:
            # Find words/phrases containing the partial
            words = chunk.content.split()
            for i, word in enumerate(words):
                if partial_lower in word.lower():
                    # Get context (word + next 2 words)
                    phrase_words = words[i:i+3]
                    phrase = ' '.join(phrase_words).strip('.,;:!?')
                    if phrase:
                        suggestions.add(phrase)
                    
                    if len(suggestions) >= limit:
                        break
            
            if len(suggestions) >= limit:
                break
        
        return list(suggestions)[:limit]
