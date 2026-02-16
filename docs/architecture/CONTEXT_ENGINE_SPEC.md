# NAVIG Ultra Context Engine — Design Spec

> Version 1.0 · February 2026

## Overview

A unified context/indexing architecture providing Copilot/Cursor-level workspace awareness across three surfaces:

| Surface | Language | Storage | Query Budget |
|---------|----------|---------|-------------|
| **navig-bridge** (VS Code) | TypeScript | In-memory BM25 | 25 KB |
| **Gistium** (VS Code) | TypeScript | In-memory BM25 | 25 KB |
| **navig CLI** | Python | SQLite FTS5 + vectors | 16 KB |

All share the same root/ignore model, chunking strategy, and retrieval semantics.

---

## 1. Shared Root & Type Model

### Always-index candidate roots (per project)

```
Code:        src/**, app/**, backend/**, lib/**, pkg/**, cmd/**
Docs:        .navig/wiki/**, .navig/knowledge/**
Plans:       .navig/plans/**
Memory:      .navig/memory/**
Identity:    ~/.navig/workspace/** (SOUL.md, USER.md, IDENTITY.md only)
```

### Never-index (built-in denylist)

```
node_modules, dist, build, .git, .venv, .idea, .vscode,
coverage, __pycache__, .next, .nuxt, .turbo, .cache,
vendor, target, *.min.js, *.min.css, package-lock.json,
pnpm-lock.yaml, yarn.lock, *.map, *.woff*, *.ttf, *.ico,
*.png, *.jpg, *.gif, *.svg, *.mp4, *.pdf, *.zip, *.vsix
```

### Content-type classifier

| Type | Extensions / Paths |
|------|-------------------|
| `code` | .py .ts .tsx .js .jsx .rs .go .java .php .sh .c .cpp .cs .rb .swift .dart .lua .kt .scala |
| `config` | .yaml .yml .json .toml .ini .env |
| `docs` | .md .txt .rst .mdx under docs/ or root |
| `wiki` | any .md under .navig/wiki/ |
| `plans` | any file under .navig/plans/ |
| `memory` | any file under .navig/memory/ or ~/.navig/workspace/ |

### Ignore resolution order

1. Built-in denylist (hardcoded)
2. `.navigignore` (project-level, gitignore syntax)
3. `.gitignore` (fallback if no .navigignore)
4. VS Code `files.exclude` (for VS Code engines only)

---

## 2. Shared Chunking Strategy

### Code files

- **Primary**: 80-line chunks with 10-line overlap
- **Fallback**: Function/class boundary detection (Python `def`/`class`, TS/JS `function`/`class`/`export`)
- Target: ~1-2 KB per chunk
- Preserve syntax unit integrity when cheap

### Docs/wiki/knowledge (.md, .txt, .rst)

- Chunk by headings (##, ###) and paragraphs
- Keep front-matter + first heading in chunk 0
- Target: ~400 tokens (~1.6 KB)

### Plans/memory

- Chunk by top-level headings
- Prioritize summaries, decisions, TODOs
- Smaller chunks (~200-300 tokens) for precision

### Chunk metadata (all types)

```
file_path:     relative path from workspace root
language:      detected language/type
start_line:    1-based start line
end_line:      1-based end line
content_type:  code | config | docs | wiki | plans | memory
section_title: nearest heading (for .md) or function name (for code)
char_count:    content length
content_hash:  FNV-1a hash of chunk content
```

---

## 3. Index Storage & Retrieval

### VS Code (in-memory, ephemeral)

- `fileIndex`: Map<path, FileMetadata>
- `chunks[]`: FileChunk array
- `invertedIndex`: Map<term, Set<chunkId>>
- `chunkTermFreqs`: Map<chunkId, Map<term, frequency>>
- `docFreq`: Map<term, count>

### CLI (SQLite, persistent)

- DB: `.navig/project_index.db`
- Tables:
  - `files(path, content_hash, size, content_type, last_indexed_at)`
  - `chunks(id, file_path, chunk_index, content, start_line, end_line, content_type, section_title, char_count)`
  - `chunks_fts` (FTS5 virtual table on chunks.content)
  - `embedding_cache(chunk_id, vector BLOB)` (optional)

### Retrieval: BM25 + optional vectors

- **Tokenize**: camelCase split → lowercase → filter (1 < len < 60)
- **BM25 params**: k1=1.2, b=0.75
- **Per-file cap**: max 3 chunks per file per query
- **File-type boost**: code=1.3×, config=1.0×, docs=0.8×, wiki=1.1×, plans=0.9×
- **Hybrid (CLI only)**: 70% vector + 30% BM25 when embeddings available

---

## 4. Limits & Safeguards

| Parameter | VS Code | CLI | Configurable |
|-----------|---------|-----|-------------|
| maxFiles (metadata) | 8,000 | 10,000 | Yes |
| maxChunkedFiles | 3,000 | 5,000 | Yes |
| maxTotalChunks | 30,000 | 50,000 | Yes |
| maxFileSize | 1 MB | 2 MB | Yes |
| perQueryBudget | 25 KB | 16 KB | Yes |
| cacheTTL | 5 min | 10 min | Yes |
| debounceMs | 1,000 | 1,500 | No |

---

## 5. Performance Targets

| Metric | Target |
|--------|--------|
| Initial full scan (1000 files) | < 3s |
| Incremental update (1 file) | < 100ms |
| BM25 query (30K chunks) | < 50ms |
| Memory footprint (5000 files) | < 80 MB |

---

## 6. Configuration

### VS Code (settings.json)

```json
{
  "navig-bridge.context.enabled": true,
  "navig-bridge.context.autoInject": true,
  "navig-bridge.context.roots": [],
  "navig-bridge.context.allowFullRepoIndex": true,
  "navig-bridge.context.maxQueryBudget": 25000,
  "navig-bridge.context.maxFiles": 8000,
  "navig-bridge.context.maxChunkedFiles": 3000
}
```

### CLI (.navig/config.yaml)

```yaml
context_builder:
  include_project_index: true
  project_index_top_k: 10
  project_index_max_chars: 12000
  max_context_chars: 32000
```

---

## 7. Implementation Status

| Component | File | Status |
|-----------|------|--------|
| navig-bridge ContextService | `src/context/ContextService.ts` | ✅ Complete (1247 lines) |
| navig-bridge chat wiring | `extension.ts`, `chatViewProvider.ts`, `vscodeLlmBackend.ts` | ✅ Complete |
| navig-bridge config section | `package.json` → "Context Engine" | ✅ Complete (7 settings) |
| navig CLI ProjectIndexer | `navig/memory/project_indexer.py` | ✅ Complete (500+ lines) |
| navig CLI ContextBuilder | `navig/memory/context_builder.py` | ✅ Updated (project_files section) |
| Tests | `tests/test_project_indexer.py` | ✅ 35 tests passing |
| Gistium ContextService | `apps/vscode/src/services/ContextService.ts` | ✅ Reference implementation |

