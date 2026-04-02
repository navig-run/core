"""
ProjectIndexer — Source-code-aware file indexer for NAVIG CLI.

Indexes **all** project source files (not just .md/.txt like MemoryIndexer)
into a local SQLite FTS5 database for fast BM25 retrieval.

Architecture:
    - Content-type-aware chunking (code: 80 lines/10 overlap, docs: 50/5)
    - SHA-256 incremental hashing — skips unchanged files
    - .navigignore + .gitignore support
    - SQLite FTS5 for keyword search (BM25 ranking)
    - Per-file cap of 3 chunks per query result
    - Content-type priority multipliers

Storage: .navig/project_index.db (project-local)

Usage:
    indexer = ProjectIndexer(Path('/path/to/project'))
    indexer.scan()                       # full scan
    indexer.update_incremental()          # rescan changed files only
    results = indexer.search('login auth handler', top_k=10)
    stats = indexer.stats()
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("navig.memory.project_indexer")


# ============================================================================
# Configuration
# ============================================================================


@dataclass
class ProjectIndexConfig:
    """Tuning knobs for the project indexer."""

    # Chunking
    code_chunk_lines: int = 80
    code_chunk_overlap: int = 10
    doc_chunk_lines: int = 50
    doc_chunk_overlap: int = 5

    # Limits
    max_files: int = 8_000
    max_chunked_files: int = 3_000
    max_total_chunks: int = 30_000
    max_file_size: int = 1_000_000  # 1 MB

    # Query
    max_results: int = 15
    max_chunks_per_file: int = 3
    max_chars: int = 25_000

    # BM25 tuning
    bm25_k1: float = 1.2
    bm25_b: float = 0.75


# ============================================================================
# Content type classification
# ============================================================================

CONTENT_TYPES = ("code", "config", "docs", "wiki", "plans", "memory")

_CODE_EXTS: set[str] = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".scala",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".php",
    ".swift",
    ".dart",
    ".lua",
    ".r",
    ".jl",
    ".vue",
    ".svelte",
    ".astro",
    ".sql",
    ".graphql",
    ".gql",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".html",
    ".css",
    ".scss",
    ".less",
    ".sass",
    ".prisma",
    ".proto",
}

_CONFIG_EXTS: set[str] = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".ini",
    ".env",
}

_DOCS_EXTS: set[str] = {
    ".md",
    ".mdx",
    ".txt",
    ".rst",
}

_INDEXABLE_EXTS: set[str] = _CODE_EXTS | _CONFIG_EXTS | _DOCS_EXTS

_INDEXABLE_FILENAMES: set[str] = {
    "dockerfile",
    "makefile",
    "cmakelists.txt",
    "gemfile",
    "rakefile",
    "procfile",
    "vagrantfile",
    ".env",
    ".gitignore",
    ".navigignore",
    ".editorconfig",
}


def classify_content_type(rel_path: str) -> str:
    """Classify a file path into a content type."""
    lower = rel_path.lower().replace("\\", "/")

    # .navig brain sub-dirs
    if "/.navig/" in f"/{lower}" or lower.startswith(".navig/"):
        if "/wiki/" in lower:
            return "wiki"
        if "/plans/" in lower or "/briefs/" in lower:
            return "plans"
        if "/memory/" in lower or "/workspace/" in lower:
            return "memory"

    ext = os.path.splitext(lower)[1]
    basename = os.path.basename(lower)

    # Config files by name (e.g. .env, .editorconfig)
    if basename in (".env", ".editorconfig") or basename.startswith(".env."):
        return "config"

    if ext in _DOCS_EXTS:
        return "docs"
    if ext in _CONFIG_EXTS:
        return "config"
    return "code"


def content_type_priority(ct: str) -> float:
    """Priority multiplier for BM25 score boosting."""
    return {
        "code": 1.0,
        "config": 0.8,
        "docs": 0.9,
        "wiki": 1.1,
        "plans": 1.2,
        "memory": 0.7,
    }.get(ct, 0.8)


# ============================================================================
# Ignore rules (.navigignore / .gitignore)
# ============================================================================

DEFAULT_EXCLUDES: list[str] = [
    ".git",
    "node_modules",
    ".next",
    ".nuxt",
    "dist",
    "build",
    ".cache",
    ".vscode",
    ".idea",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
    "vendor",
    "target",
    ".turbo",
    "coverage",
    ".nyc_output",
    "*.min.js",
    "*.min.css",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "*.lock",
    "*.map",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.ico",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.mp4",
    "*.mp3",
    "*.pdf",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.vsix",
    ".venv",
    ".env.local",
    ".tsbuildinfo",
]


def _load_ignore_rules(project_root: Path) -> list[str]:
    """Load .navigignore then .gitignore patterns."""
    patterns = list(DEFAULT_EXCLUDES)
    for name in (".navigignore", ".gitignore"):
        p = project_root / name
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
    return patterns


def _is_ignored(rel_path: str, ignore_patterns: list[str], is_dir: bool = False) -> bool:
    """Check if a relative path matches any ignore pattern.

    ``.navig/plans/`` and ``.navig/wiki/`` are force-included even when
    ``.navig`` appears in ``.gitignore`` — these contain plan and wiki
    content that must be searchable.
    """
    # Force-include whitelist: always index plans and wiki inside .navig
    _norm = rel_path.replace("\\", "/")
    if (
        _norm == ".navig"
        or _norm.startswith(".navig/plans")
        or _norm.startswith(".navig/wiki")
    ):
        return False

    parts = _norm.split("/")
    for pattern in ignore_patterns:
        pat = pattern.rstrip("/")
        # Match against any path component
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
        # Match against full relative path
        if fnmatch.fnmatch(rel_path.replace("\\", "/"), pat):
            return True
        if fnmatch.fnmatch(rel_path.replace("\\", "/"), f"**/{pat}"):
            return True
    return False


def _is_indexable(rel_path: str) -> bool:
    """Check if a file should be indexed based on extension."""
    lower = os.path.basename(rel_path).lower()
    if lower in _INDEXABLE_FILENAMES:
        return True
    ext = os.path.splitext(lower)[1]
    return ext in _INDEXABLE_EXTS


# ============================================================================
# Chunking
# ============================================================================


@dataclass
class Chunk:
    """A chunk of file content."""

    file_path: str
    content: str
    start_line: int
    end_line: int
    content_type: str
    section_title: str
    content_hash: str


def _extract_section_title(lines: list[str], content_type: str) -> str:
    """Extract a meaningful section title from chunk lines."""
    for line in lines[:5]:
        stripped = line.strip()
        if not stripped:
            continue
        if content_type == "docs" and stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:80]
        if content_type == "code":
            # Function/class definitions
            m = re.match(
                r"^(?:export\s+)?(?:async\s+)?(?:def|function|class|interface|type|enum|const|let|var|pub\s+fn|fn|func|impl)\s+(\w+)",
                stripped,
            )
            if m:
                return m.group(1)[:80]
    return ""


def chunk_file(
    rel_path: str,
    content: str,
    config: ProjectIndexConfig,
) -> list[Chunk]:
    """Split file content into overlapping chunks."""
    ct = classify_content_type(rel_path)

    if ct in ("code",):
        chunk_size = config.code_chunk_lines
        overlap = config.code_chunk_overlap
    else:
        chunk_size = config.doc_chunk_lines
        overlap = config.doc_chunk_overlap

    lines = content.splitlines()
    if not lines:
        return []

    chunks: list[Chunk] = []
    step = max(1, chunk_size - overlap)
    i = 0

    while i < len(lines):
        end = min(i + chunk_size, len(lines))
        chunk_lines = lines[i:end]
        chunk_text = "\n".join(chunk_lines)

        if chunk_text.strip():
            h = hashlib.sha256(chunk_text.encode("utf-8", errors="replace")).hexdigest()[:16]
            title = _extract_section_title(chunk_lines, ct)
            chunks.append(
                Chunk(
                    file_path=rel_path,
                    content=chunk_text,
                    start_line=i + 1,
                    end_line=end,
                    content_type=ct,
                    section_title=title,
                    content_hash=h,
                )
            )

        if end >= len(lines):
            break
        i += step

    return chunks


# ============================================================================
# Search result
# ============================================================================


@dataclass
class ProjectSearchResult:
    """A single search result from the project index."""

    file_path: str
    content: str
    start_line: int
    end_line: int
    content_type: str
    section_title: str
    score: float
    rank: int = 0

    def to_context_string(self) -> str:
        """Format as LLM-friendly context block."""
        header = f"--- {self.file_path}:{self.start_line}-{self.end_line}"
        if self.section_title:
            header += f"  ({self.section_title})"
        return f"{header}\n{self.content}"


# ============================================================================
# ProjectIndexer
# ============================================================================


class ProjectIndexer:
    """
    Indexes project source files into SQLite FTS5 for BM25 retrieval.

    Usage:
        indexer = ProjectIndexer(Path('.'))
        indexer.scan()
        results = indexer.search('auth login handler')
    """

    DB_NAME = "project_index.db"

    def __init__(
        self,
        project_root: Path,
        config: ProjectIndexConfig | None = None,
    ):
        self.project_root = project_root.resolve()
        self.config = config or ProjectIndexConfig()
        self._ignore_patterns = _load_ignore_rules(self.project_root)

        # DB lives in .navig/
        navig_dir = self.project_root / ".navig"
        navig_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = navig_dir / self.DB_NAME

        self._conn: sqlite3.Connection | None = None
        self._file_hashes: dict[str, str] = {}  # rel_path -> content_hash
        self._last_scan_duration: float | None = None

        self._ensure_db()

    # ----------------------------------------------------------------
    # Database setup
    # ----------------------------------------------------------------

    def _ensure_db(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS file_meta (
                path TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                line_count INTEGER NOT NULL,
                char_count INTEGER NOT NULL,
                mtime REAL NOT NULL,
                indexed_at REAL NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                file_path,
                content,
                start_line UNINDEXED,
                end_line UNINDEXED,
                content_type UNINDEXED,
                section_title,
                content_hash UNINDEXED,
                tokenize='porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """
        )
        conn.commit()

        # Load existing file hashes for incremental checks
        for row in conn.execute("SELECT path, content_hash FROM file_meta"):
            self._file_hashes[row[0]] = row[1]

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-8000")  # 8 MB
        return self._conn

    # ----------------------------------------------------------------
    # Discovery
    # ----------------------------------------------------------------

    def _discover_files(self) -> list[tuple[str, float]]:
        """Walk the project tree and return (rel_path, mtime) tuples."""
        files: list[tuple[str, float]] = []
        max_files = self.config.max_files

        for dirpath, dirnames, filenames in os.walk(self.project_root):
            # Skip ignored directories (in-place filter)
            rel_dir = os.path.relpath(dirpath, self.project_root)
            if rel_dir != "." and _is_ignored(rel_dir, self._ignore_patterns, is_dir=True):
                dirnames.clear()
                continue

            # Filter out ignored subdirectories
            dirnames[:] = [
                d
                for d in dirnames
                if not _is_ignored(
                    os.path.relpath(os.path.join(dirpath, d), self.project_root),
                    self._ignore_patterns,
                    is_dir=True,
                )
            ]

            for fname in filenames:
                if len(files) >= max_files:
                    break

                fpath = os.path.join(dirpath, fname)
                rel = os.path.relpath(fpath, self.project_root)

                if _is_ignored(rel, self._ignore_patterns):
                    continue
                if not _is_indexable(rel):
                    continue

                try:
                    st = os.stat(fpath)
                    if st.st_size > self.config.max_file_size:
                        continue
                    files.append((rel, st.st_mtime))
                except OSError:
                    continue

            if len(files) >= max_files:
                break

        return files

    # ----------------------------------------------------------------
    # Full scan
    # ----------------------------------------------------------------

    def scan(self) -> dict[str, int]:
        """
        Full scan: discover files, read, chunk, and index.

        Returns:
            Stats dict with files_discovered, files_indexed, chunks_created, duration_s.
        """
        t0 = time.monotonic()
        conn = self._get_conn()

        # Phase 1: discover
        discovered = self._discover_files()
        logger.info("[ProjectIndexer] Discovered %d files", len(discovered))

        # Phase 2: read + chunk + index
        chunks_created = 0
        files_indexed = 0
        files_skipped = 0

        # Clear old data for full scan
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM file_meta")
        conn.commit()
        self._file_hashes.clear()

        # Limit chunked files
        to_chunk = discovered[: self.config.max_chunked_files]

        for rel_path, mtime in to_chunk:
            abs_path = self.project_root / rel_path

            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("[ProjectIndexer] Read error %s: %s", rel_path, e)
                continue

            content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[
                :16
            ]
            ct = classify_content_type(rel_path)
            line_count = content.count("\n") + 1

            # Store file metadata
            conn.execute(
                "INSERT OR REPLACE INTO file_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rel_path,
                    ct,
                    content_hash,
                    line_count,
                    len(content),
                    mtime,
                    time.time(),
                ),
            )
            self._file_hashes[rel_path] = content_hash

            # Chunk and index
            chunks = chunk_file(rel_path, content, self.config)
            if chunks_created + len(chunks) > self.config.max_total_chunks:
                chunks = chunks[: self.config.max_total_chunks - chunks_created]

            for chunk in chunks:
                conn.execute(
                    "INSERT INTO chunks (file_path, content, start_line, end_line, content_type, section_title, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.file_path,
                        chunk.content,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content_type,
                        chunk.section_title,
                        chunk.content_hash,
                    ),
                )
                chunks_created += 1

            files_indexed += 1

            if chunks_created >= self.config.max_total_chunks:
                break

        conn.commit()

        duration = time.monotonic() - t0
        self._last_scan_duration = duration

        # Store scan metadata
        conn.execute(
            "INSERT OR REPLACE INTO index_meta VALUES ('last_scan', ?)",
            (str(time.time()),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO index_meta VALUES ('last_scan_duration', ?)",
            (str(round(duration, 2)),),
        )
        conn.commit()

        stats = {
            "files_discovered": len(discovered),
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
            "chunks_created": chunks_created,
            "duration_s": round(duration, 2),
        }
        logger.info("[ProjectIndexer] Scan complete: %s", stats)
        return stats

    # ----------------------------------------------------------------
    # Incremental update
    # ----------------------------------------------------------------

    def update_incremental(self) -> dict[str, int]:
        """
        Rescan only changed/new files (mtime + hash check).

        Returns:
            Stats dict with files_checked, files_updated, chunks_added, duration_s.
        """
        t0 = time.monotonic()
        conn = self._get_conn()

        discovered = self._discover_files()
        discovered_set = {rel for rel, _ in discovered}

        files_updated = 0
        chunks_added = 0

        # Remove deleted files
        existing_paths = set(self._file_hashes.keys())
        deleted = existing_paths - discovered_set
        for rel in deleted:
            conn.execute("DELETE FROM chunks WHERE file_path = ?", (rel,))
            conn.execute("DELETE FROM file_meta WHERE path = ?", (rel,))
            self._file_hashes.pop(rel, None)

        # Check for new/changed files
        for rel_path, mtime in discovered:
            abs_path = self.project_root / rel_path

            # Quick mtime check — skip if mtime unchanged (avoids file read)
            old_hash = self._file_hashes.get(rel_path)
            if old_hash is not None:
                row = conn.execute(
                    "SELECT mtime FROM file_meta WHERE path = ?", (rel_path,)
                ).fetchone()
                if row and row[0] == mtime:
                    continue  # mtime unchanged → skip

            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            new_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]

            if new_hash == old_hash:
                # mtime changed but content didn't — update mtime only
                conn.execute("UPDATE file_meta SET mtime = ? WHERE path = ?", (mtime, rel_path))
                continue

            # File changed — re-index it
            conn.execute("DELETE FROM chunks WHERE file_path = ?", (rel_path,))
            conn.execute("DELETE FROM file_meta WHERE path = ?", (rel_path,))

            ct = classify_content_type(rel_path)
            line_count = content.count("\n") + 1

            conn.execute(
                "INSERT OR REPLACE INTO file_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rel_path, ct, new_hash, line_count, len(content), mtime, time.time()),
            )
            self._file_hashes[rel_path] = new_hash

            chunks = chunk_file(rel_path, content, self.config)
            for chunk in chunks:
                conn.execute(
                    "INSERT INTO chunks (file_path, content, start_line, end_line, content_type, section_title, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.file_path,
                        chunk.content,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content_type,
                        chunk.section_title,
                        chunk.content_hash,
                    ),
                )
                chunks_added += 1

            files_updated += 1

        conn.commit()

        duration = time.monotonic() - t0
        stats = {
            "files_checked": len(discovered),
            "files_deleted": len(deleted),
            "files_updated": files_updated,
            "chunks_added": chunks_added,
            "duration_s": round(duration, 2),
        }
        logger.debug("[ProjectIndexer] Incremental update: %s", stats)
        return stats

    # ----------------------------------------------------------------
    # Search
    # ----------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int | None = None,
        max_chars: int | None = None,
        content_type_filter: str | None = None,
    ) -> list[ProjectSearchResult]:
        """
        BM25 search over the project index.

        Returns ranked chunks, limited by top_k and max_chars, with
        per-file cap of max_chunks_per_file.

        Args:
            query: Natural language search query.
            top_k: Max results (defaults to config.max_results).
            max_chars: Max total characters (defaults to config.max_chars).
            content_type_filter: Restrict results to a single content type
                (e.g. 'wiki', 'code', 'docs', 'plans'). None = all types.

        Returns:
            List of ProjectSearchResult sorted by score descending.
        """
        if not query.strip():
            return []

        top_k = top_k or self.config.max_results
        max_chars = max_chars or self.config.max_chars
        conn = self._get_conn()

        # Clean up query for FTS5
        safe_query = self._sanitize_fts_query(query)
        if not safe_query:
            return []

        try:
            if content_type_filter:
                rows = conn.execute(
                    """
                    SELECT file_path, content, start_line, end_line, content_type,
                           section_title, bm25(chunks) AS score
                    FROM chunks
                    WHERE chunks MATCH ?
                      AND content_type = ?
                    ORDER BY bm25(chunks)
                    LIMIT ?
                    """,
                    (safe_query, content_type_filter, top_k * 3),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT file_path, content, start_line, end_line, content_type,
                           section_title, bm25(chunks) AS score
                    FROM chunks
                    WHERE chunks MATCH ?
                    ORDER BY bm25(chunks)
                    LIMIT ?
                    """,
                    (safe_query, top_k * 3),  # fetch extra for per-file dedup
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("[ProjectIndexer] FTS5 query error: %s", e)
            return []

        # Apply content-type boosting + per-file cap
        results: list[ProjectSearchResult] = []
        file_counts: dict[str, int] = {}
        total_chars = 0

        for row in rows:
            fpath, content, sl, el, ct, title, raw_score = row
            boost = content_type_priority(ct)
            score = abs(raw_score) * boost  # BM25 returns negative scores

            fc = file_counts.get(fpath, 0)
            if fc >= self.config.max_chunks_per_file:
                continue

            if total_chars + len(content) > max_chars:
                continue

            results.append(
                ProjectSearchResult(
                    file_path=fpath,
                    content=content,
                    start_line=sl,
                    end_line=el,
                    content_type=ct,
                    section_title=title,
                    score=round(score, 4),
                )
            )
            file_counts[fpath] = fc + 1
            total_chars += len(content)

            if len(results) >= top_k:
                break

        # Assign ranks
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Convert natural language query to FTS5 safe query."""
        # Remove special FTS5 characters
        cleaned = re.sub(r"[^\w\s]", " ", query)
        tokens = cleaned.split()
        # Keep meaningful tokens
        tokens = [t for t in tokens if len(t) >= 2]
        if not tokens:
            return ""
        # Join with OR for broader matching
        return " OR ".join(tokens)

    # ----------------------------------------------------------------
    # Context assembly (for LLM injection)
    # ----------------------------------------------------------------

    def get_context(
        self,
        query: str,
        max_chars: int = 8_000,
        content_type_filter: str | None = None,
        top_k: int = 8,
    ) -> str:
        """
        Retrieve and format the most relevant chunks as an LLM context string.

        Args:
            query: Natural language query.
            max_chars: Budget for total context characters.
            content_type_filter: Restrict to a content type (e.g. 'wiki').
            top_k: Maximum chunks to include.

        Returns:
            Formatted multi-block context string, or empty string if nothing found.
        """
        results = self.search(
            query,
            top_k=top_k,
            max_chars=max_chars,
            content_type_filter=content_type_filter,
        )
        if not results:
            return ""
        return "\n\n".join(r.to_context_string() for r in results)

    # ----------------------------------------------------------------
    # Stats
    # ----------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return index statistics."""
        conn = self._get_conn()

        file_count = conn.execute("SELECT COUNT(*) FROM file_meta").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        total_chars = conn.execute("SELECT COALESCE(SUM(char_count), 0) FROM file_meta").fetchone()[
            0
        ]

        last_scan = None
        row = conn.execute("SELECT value FROM index_meta WHERE key = 'last_scan'").fetchone()
        if row:
            last_scan = float(row[0])

        return {
            "file_count": file_count,
            "chunk_count": chunk_count,
            "total_chars": total_chars,
            "total_size_kb": round(total_chars / 1024, 1),
            "db_path": str(self._db_path),
            "last_scan": last_scan,
            "last_scan_duration": self._last_scan_duration,
        }

    # ----------------------------------------------------------------
    # File tree summary
    # ----------------------------------------------------------------

    def file_tree_summary(self, max_lines: int = 80) -> str:
        """Generate a compact file tree for LLM context injection."""
        conn = self._get_conn()
        rows = conn.execute("SELECT path, content_type FROM file_meta ORDER BY path").fetchall()

        if not rows:
            return "(no files indexed)"

        lines: list[str] = [f"Project: {self.project_root.name} ({len(rows)} files)"]

        # Group by top-level directory
        groups: dict[str, list[str]] = {}
        for path, ct in rows:
            parts = path.replace("\\", "/").split("/")
            key = parts[0] if len(parts) > 1 else "."
            groups.setdefault(key, []).append(path)

        for folder in sorted(groups.keys()):
            files = groups[folder]
            lines.append(f"  {folder}/ ({len(files)} files)")
            if len(lines) >= max_lines:
                lines.append(f"  ... and {len(rows) - sum(len(v) for v in groups.values())} more")
                break

        return "\n".join(lines[:max_lines])

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            self._conn = None

    def drop_index(self) -> None:
        """Delete the entire index database."""
        self.close()
        try:
            self._db_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("[ProjectIndexer] Failed to delete index: %s", e)
        self._file_hashes.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
