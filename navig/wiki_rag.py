"""Wiki RAG (Retrieval-Augmented Generation) Module

Provides semantic search and context retrieval for the NAVIG wiki.
Uses lightweight text search with TF-IDF and BM25 algorithms.

For production use with large knowledge bases, consider:
- ChromaDB for vector embeddings
- sentence-transformers for semantic embeddings
- FAISS for efficient similarity search

This module provides a lightweight fallback that works without extra dependencies.
"""

import re
import math
from pathlib import Path
from typing import TYPE_CHECKING, List, Dict, Any, Optional, Tuple
from collections import Counter, defaultdict
from dataclasses import dataclass
import json

from navig import console_helper as ch

if TYPE_CHECKING:
    from navig.memory.project_indexer import ProjectIndexer


@dataclass
class WikiDocument:
    """Represents a wiki document for indexing."""
    path: str
    title: str
    content: str
    folder: str
    chunks: List[str] = None
    
    def __post_init__(self):
        if self.chunks is None:
            self.chunks = self._chunk_content()
    
    def _chunk_content(self, chunk_size: int = 500, overlap: int = 100) -> List[str]:
        """Split content into overlapping chunks for better retrieval."""
        if len(self.content) <= chunk_size:
            return [self.content]
        
        chunks = []
        start = 0
        while start < len(self.content):
            end = start + chunk_size
            chunk = self.content[start:end]
            
            # Try to break at sentence boundary
            if end < len(self.content):
                last_period = chunk.rfind('. ')
                if last_period > chunk_size // 2:
                    chunk = chunk[:last_period + 1]
                    end = start + last_period + 1
            
            chunks.append(chunk.strip())
            start = end - overlap
        
        return chunks


class TextTokenizer:
    """Simple text tokenizer for search."""
    
    # Common stop words to filter
    STOP_WORDS = {
        'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
        'this', 'that', 'these', 'those', 'it', 'its', 'as', 'if', 'when',
        'than', 'so', 'no', 'not', 'only', 'own', 'same', 'such', 'too',
        'very', 'just', 'also', 'now', 'here', 'there', 'where', 'how',
        'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
        'some', 'any', 'no', 'nor', 'not', 'only', 'over', 'under',
    }
    
    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Tokenize text into words."""
        # Convert to lowercase and split on non-alphanumeric
        words = re.findall(r'\b[a-z0-9]+\b', text.lower())
        # Filter stop words and short words
        return [w for w in words if w not in TextTokenizer.STOP_WORDS and len(w) > 2]


class BM25Index:
    """BM25 index for text search.
    
    BM25 is a bag-of-words retrieval function that ranks documents
    based on query terms appearing in each document.
    """
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """Initialize BM25 index.
        
        Args:
            k1: Term frequency saturation parameter (1.2-2.0)
            b: Length normalization parameter (0-1)
        """
        self.k1 = k1
        self.b = b
        self.documents: List[Tuple[WikiDocument, int]] = []  # (doc, chunk_idx)
        self.doc_freqs: Dict[str, int] = defaultdict(int)
        self.doc_lens: List[int] = []
        self.avg_doc_len: float = 0
        self.term_freqs: List[Counter] = []
    
    def index(self, documents: List[WikiDocument]):
        """Index a list of documents."""
        self.documents = []
        self.term_freqs = []
        self.doc_lens = []
        self.doc_freqs = defaultdict(int)
        
        # Index each chunk as a separate document
        for doc in documents:
            for chunk_idx, chunk in enumerate(doc.chunks):
                tokens = TextTokenizer.tokenize(chunk)
                
                self.documents.append((doc, chunk_idx))
                self.doc_lens.append(len(tokens))
                
                term_freq = Counter(tokens)
                self.term_freqs.append(term_freq)
                
                # Update document frequencies
                for term in set(tokens):
                    self.doc_freqs[term] += 1
        
        if self.doc_lens:
            self.avg_doc_len = sum(self.doc_lens) / len(self.doc_lens)
    
    def _idf(self, term: str) -> float:
        """Calculate inverse document frequency for a term."""
        n = len(self.documents)
        df = self.doc_freqs.get(term, 0)
        if df == 0:
            return 0
        return math.log((n - df + 0.5) / (df + 0.5) + 1)
    
    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """Search the index.
        
        Args:
            query: Search query
            top_k: Number of results to return
            
        Returns:
            List of search results with scores
        """
        query_tokens = TextTokenizer.tokenize(query)
        
        if not query_tokens:
            return []
        
        scores = []
        
        for idx, ((doc, chunk_idx), doc_len, term_freq) in enumerate(
            zip(self.documents, self.doc_lens, self.term_freqs)
        ):
            score = 0
            
            for term in query_tokens:
                if term not in term_freq:
                    continue
                
                tf = term_freq[term]
                idf = self._idf(term)
                
                # BM25 formula
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len)
                score += idf * numerator / denominator
            
            if score > 0:
                scores.append((score, idx, doc, chunk_idx))
        
        # Sort by score descending
        scores.sort(key=lambda x: x[0], reverse=True)
        
        # Deduplicate by document path (keep highest scoring chunk)
        seen_paths = set()
        results = []
        
        for score, idx, doc, chunk_idx in scores:
            if doc.path in seen_paths:
                continue
            seen_paths.add(doc.path)
            
            results.append({
                'path': doc.path,
                'title': doc.title,
                'folder': doc.folder,
                'score': round(score, 4),
                'chunk': doc.chunks[chunk_idx][:300] + '...' if len(doc.chunks[chunk_idx]) > 300 else doc.chunks[chunk_idx],
                'chunk_index': chunk_idx,
                'total_chunks': len(doc.chunks)
            })
            
            if len(results) >= top_k:
                break
        
        return results


class WikiRAG:
    """RAG system for wiki knowledge base.

    Provides semantic search and context retrieval for AI assistants.

    When *project_indexer* is supplied the in-memory BM25 logic is bypassed
    entirely — all search/context calls are delegated to the SQLite FTS5
    engine in ProjectIndexer, filtered to ``content_type='wiki'``.
    This is the preferred path when a project context exists.

    The legacy in-memory path remains as a standalone fallback (e.g. for
    ``navig wiki`` commands run outside a project that has been indexed).
    """

    def __init__(
        self,
        wiki_path: Path,
        project_indexer: Optional["ProjectIndexer"] = None,
    ):
        """Initialize Wiki RAG.

        Args:
            wiki_path: Path to wiki directory.
            project_indexer: Optional unified ProjectIndexer instance.  When
                provided, search/context are delegated to it (wiki type filter).
        """
        self.wiki_path = Path(wiki_path)
        self._project_indexer = project_indexer

        if project_indexer is not None:
            # Unified path — no in-memory index needed
            self.index = None
            self.documents = []
            self.index_file = self.wiki_path / '.meta' / 'rag_index.json'
            return

        self.index = BM25Index()
        self.documents: List[WikiDocument] = []
        self.index_file = self.wiki_path / '.meta' / 'rag_index.json'

        self._load_or_build_index()
    
    def _load_or_build_index(self):
        """Load existing index or build new one."""
        if self.index_file.exists():
            try:
                self._load_index()
                return
            except Exception as e:
                ch.dim(f"Could not load index: {e}")
        
        self.rebuild_index()
    
    def _load_index(self):
        """Load index from file."""
        with open(self.index_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.documents = [
            WikiDocument(
                path=d['path'],
                title=d['title'],
                content=d['content'],
                folder=d['folder'],
                chunks=d['chunks']
            )
            for d in data.get('documents', [])
        ]
        
        self.index.index(self.documents)
    
    def _save_index(self):
        """Save index to file."""
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            'documents': [
                {
                    'path': d.path,
                    'title': d.title,
                    'content': d.content,
                    'folder': d.folder,
                    'chunks': d.chunks
                }
                for d in self.documents
            ]
        }
        
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    def rebuild_index(self):
        """Rebuild the search index from wiki pages.

        No-op when using the unified ProjectIndexer backend — call
        ``project_indexer.scan()`` or ``update_incremental()`` instead.
        """
        if self._project_indexer is not None:
            return  # unified backend — caller should use project_indexer.scan()

        self.documents = []

        if not self.wiki_path.exists():
            return
        
        for md_file in self.wiki_path.glob('**/*.md'):
            rel_path = md_file.relative_to(self.wiki_path)
            
            # Skip hidden folders
            if any(part.startswith('.') for part in rel_path.parts):
                continue
            
            try:
                content = md_file.read_text(encoding='utf-8')
                
                # Extract title from first heading
                title = md_file.stem
                for line in content.split('\n'):
                    if line.startswith('# '):
                        title = line[2:].strip()
                        break
                
                folder = str(rel_path.parent).replace('\\', '/')
                
                doc = WikiDocument(
                    path=str(rel_path).replace('\\', '/'),
                    title=title,
                    content=content,
                    folder=folder
                )
                self.documents.append(doc)
                
            except Exception as e:
                ch.dim(f"Could not index {rel_path}: {e}")
        
        self.index.index(self.documents)
        self._save_index()
    
    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """Search wiki for relevant content.

        Args:
            query: Natural language search query
            top_k: Maximum results to return

        Returns:
            List of relevant documents with scores and snippets
        """
        if self._project_indexer is not None:
            results = self._project_indexer.search(
                query, top_k=top_k, content_type_filter="wiki"
            )
            return [
                {
                    "path": r.file_path,
                    "title": r.section_title or Path(r.file_path).stem,
                    "folder": str(Path(r.file_path).parent).replace("\\", "/"),
                    "score": r.score,
                    "chunk": r.content[:300] + "..." if len(r.content) > 300 else r.content,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
                for r in results
            ]
        return self.index.search(query, top_k)
    
    def get_context(self, query: str, max_tokens: int = 2000) -> str:
        """Get relevant context for an AI query.

        Retrieves and concatenates the most relevant wiki content
        for use as context in AI prompts.

        Args:
            query: The question or topic to find context for
            max_tokens: Approximate maximum tokens to return

        Returns:
            Formatted context string for AI consumption
        """
        char_limit = max_tokens * 4  # ~4 chars per token

        if self._project_indexer is not None:
            ctx = self._project_indexer.get_context(
                query,
                max_chars=char_limit,
                content_type_filter="wiki",
            )
            return ctx if ctx else "No relevant wiki content found."

        results = self.search(query, top_k=5)

        if not results:
            return "No relevant wiki content found."

        context_parts = []
        total_chars = 0

        for result in results:
            if total_chars >= char_limit:
                break

            # Find full document
            doc = next((d for d in self.documents if d.path == result['path']), None)
            if not doc:
                continue

            # Add document content
            content = doc.content
            if total_chars + len(content) > char_limit:
                content = content[:char_limit - total_chars]

            context_parts.append(f"## {doc.title}\n*Source: {doc.path}*\n\n{content}")
            total_chars += len(content)

        return "\n\n---\n\n".join(context_parts)
    
    def add_document(self, path: str, content: str, title: Optional[str] = None):
        """Add a new document to the index.
        
        Args:
            path: Document path relative to wiki root
            content: Document content
            title: Optional title (extracted from content if not provided)
        """
        if not title:
            for line in content.split('\n'):
                if line.startswith('# '):
                    title = line[2:].strip()
                    break
            if not title:
                title = Path(path).stem
        
        folder = str(Path(path).parent).replace('\\', '/')
        
        doc = WikiDocument(
            path=path,
            title=title,
            content=content,
            folder=folder
        )
        
        # Remove existing document with same path
        self.documents = [d for d in self.documents if d.path != path]
        self.documents.append(doc)
        
        # Rebuild index
        self.index.index(self.documents)
        self._save_index()
    
    def remove_document(self, path: str):
        """Remove a document from the index.
        
        Args:
            path: Document path to remove
        """
        self.documents = [d for d in self.documents if d.path != path]
        self.index.index(self.documents)
        self._save_index()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        if self._project_indexer is not None:
            s = self._project_indexer.stats()
            return {
                'backend': 'unified',
                'total_documents': s.get('file_count', 0),
                'total_chunks': s.get('chunk_count', 0),
                'total_words': 0,  # not tracked in FTS5 path
                'unique_terms': 0,
                'avg_doc_length': 0,
                'db_path': s.get('db_path', ''),
            }
        total_chunks = sum(len(d.chunks) for d in self.documents)
        total_words = sum(len(TextTokenizer.tokenize(d.content)) for d in self.documents)

        return {
            'backend': 'in_memory',
            'total_documents': len(self.documents),
            'total_chunks': total_chunks,
            'total_words': total_words,
            'unique_terms': len(self.index.doc_freqs),
            'avg_doc_length': round(self.index.avg_doc_len, 2) if self.index.avg_doc_len else 0,
        }


def get_wiki_rag(
    wiki_path: Path,
    project_root: Optional[Path] = None,
    use_unified: bool = False,
) -> WikiRAG:
    """Get or create WikiRAG instance.

    Args:
        wiki_path: Path to wiki directory.
        project_root: Project root for unified indexer (optional).
        use_unified: When True and *project_root* is given, delegates to
            ``ProjectIndexer`` (SQLite FTS5) instead of the in-memory BM25.
            Equivalent to the ``navig.context.useUnifiedIndexer`` config flag.

    Returns:
        WikiRAG instance.
    """
    if use_unified and project_root is not None:
        try:
            from navig.memory.project_indexer import ProjectIndexer
            indexer = ProjectIndexer(Path(project_root))
            return WikiRAG(wiki_path, project_indexer=indexer)
        except Exception as e:
            ch.dim(f"[WikiRAG] Unified indexer unavailable ({e}), falling back to in-memory.")
    return WikiRAG(wiki_path)
