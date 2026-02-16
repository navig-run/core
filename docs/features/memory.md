# NAVIG Memory Bank

The Memory Bank is a file-based knowledge store that enables NAVIG to manage both **system operations** (infrastructure, deployments, configs) and **life operations** (tasks, notes, routines, projects):

- **Index** Markdown files for semantic search
- **Search** using hybrid vector + keyword matching
- **Inject** relevant context into AI conversations
- **Watch** for file changes and auto-reindex

## Quick Start

```bash
# Create your memory directory
mkdir -p ~/.navig/memory

# Add knowledge files
echo "# Project Notes\nMy project uses Docker and PostgreSQL" > ~/.navig/memory/project.md

# Index the memory bank
navig memory index

# Search your knowledge
navig memory search "docker setup"
```

## Architecture

```
~/.navig/memory/
├── systems/                  # Infrastructure knowledge
│   ├── docker-config.md
│   ├── server-setup.md
│   └── deploy-workflows.md
├── life/                     # Personal operations
│   ├── daily-routines.md
│   ├── project-goals.md
│   └── meeting-notes.md
├── reference/                # Shared knowledge
│   ├── shortcuts.md
│   └── contacts.md
└── index.db                  # SQLite index (auto-generated)
```

### Chunking Strategy

Files are split into searchable chunks:
- **~400 tokens** per chunk (optimal for embedding models)
- **80-token overlap** between chunks (preserves context)
- Respects document structure (headers, paragraphs, code blocks)
- Line numbers tracked for precise citations

### Hybrid Search

Memory search combines two techniques:

| Method | Weight | Strength |
|--------|--------|----------|
| Vector similarity | 70% | Semantic understanding |
| BM25 keywords | 30% | Exact term matching |

This hybrid approach finds results even when:
- Query uses different words than the source (vector)
- Exact technical terms are important (BM25)

## CLI Commands

### Index Files

```bash
# Index all .md/.txt files
navig memory index

# Force re-index everything
navig memory index --force

# Skip embedding generation (faster, keyword-only search)
navig memory index --no-embed

# Show progress
navig memory index --verbose
```

### Search

```bash
# Basic search
navig memory search "docker networking"

# Limit results
navig memory search "nginx config" --limit 10

# Filter by file pattern
navig memory search "deploy" --file "workflows/*.md"

# Get JSON output
navig memory search "postgres" --json

# Plain output for scripting
navig memory search "backup" --plain
```

### Status & Management

```bash
# Show memory bank status
navig memory bank

# List indexed files
navig memory files

# Clear index (preserves source files)
navig memory clear-bank

# View memory usage statistics
navig memory stats
```

## File Formats

The memory bank indexes:
- `.md` - Markdown files (recommended)
- `.markdown` - Markdown files
- `.txt` - Plain text files

### Recommended Structure

Organize by domain for clarity:

**Infrastructure Knowledge:**
```markdown
# Docker Production Setup

Our production Docker config for the API service.

## Compose File
```yaml
services:
  api:
    image: myapp:latest
    ports: ["8080:8080"]
```

## Deployment Commands
```bash
docker compose up -d
docker compose logs -f
```

---
tags: [docker, deployment, production]
---
```

**Life Operations Knowledge:**
```markdown
# Daily Routines

My structured morning and evening workflows.

## Morning Routine (6:30 AM)
1. Review calendar and priorities
2. Check system alerts (navig heartbeat)
3. Process inbox to zero
4. Deep work block (2 hours)

## Weekly Review (Sunday)
- Review goals and progress
- Plan upcoming week
- Archive completed projects

---
tags: [routine, productivity, habits]
---
```

## AI Integration

The Memory Bank automatically provides context when you chat with NAVIG:

**Infrastructure queries:**
```
You: How do I deploy the app?
NAVIG: Based on your deployment notes [source: systems/deploy-workflows.md:15-23]...
```

**Life operations queries:**
```
You: What's my morning routine?
NAVIG: According to your routines [source: life/daily-routines.md:8-15], you start at 6:30 AM with...
```

### Context Injection

When processing queries, NAVIG:
1. Searches the memory bank for relevant chunks
2. Includes top results (up to 2000 tokens) in the AI prompt
3. Generates responses that cite sources

### Manual Context

You can explicitly search before asking:

```bash
# Search first
navig memory search "nginx ssl"

# Then ask with context
navig ai ask "How should I configure SSL?"
```

## Configuration

### Memory Directory

Default: `~/.navig/memory/`

Override with environment variable:
```bash
export NAVIG_MEMORY_DIR=/path/to/memory
```

### Embedding Model

Default: `all-MiniLM-L6-v2` (384 dimensions, fast)

For higher quality (but slower):
```python
# In navig/memory/manager.py
manager = MemoryManager(embedding_model="all-mpnet-base-v2")  # 768 dims
```

### Search Weights

Default: 70% vector, 30% keyword

Customize:
```python
from navig.memory import HybridSearch, SearchConfig

config = SearchConfig(
    vector_weight=0.8,  # More semantic
    keyword_weight=0.2,
)
search = HybridSearch(storage, embedding_provider, config)
```

## File Watching

Enable automatic reindexing when files change:

```python
from navig.memory import get_memory_manager, MemoryWatcher

manager = get_memory_manager()
watcher = MemoryWatcher(manager, debounce_seconds=1.5)
watcher.start()

# ... files are now watched ...

watcher.stop()
```

Or use the context manager:

```python
from navig.memory import get_memory_manager, WatcherContext

with WatcherContext(manager) as watcher:
    # Files are watched within this block
    pass
```

## API Reference

### MemoryManager

Main interface for memory operations:

```python
from navig.memory import get_memory_manager

manager = get_memory_manager()

# Index files
result = manager.index(force=False, embed=True)

# Search
response = manager.search("docker config", limit=5)

# Get context for AI
context = manager.get_context("How do I deploy?", max_tokens=2000)

# Add a new file
manager.add_file("# Notes\nContent...", "notes.md")

# Get statistics
stats = manager.get_stats()
```

### SearchResult

```python
result = response.results[0]

result.file_path      # "project.md"
result.content        # Full chunk content
result.line_start     # 15
result.line_end       # 23
result.combined_score # 0.85
result.vector_score   # 0.90
result.keyword_score  # 0.75
result.snippet        # "...relevant excerpt..."
result.citation()     # "[source: project.md:15-23]"
```

### SearchResponse

```python
response = manager.search("query")

response.results        # List of SearchResult
response.total_matches  # Number of results
response.search_time_ms # Search duration
response.as_context()   # Formatted for AI prompts
```

## Dependencies

Required:
- `numpy` - Vector operations
- `sentence-transformers` - Local embeddings (optional but recommended)

Install:
```bash
pip install numpy sentence-transformers
```

Without `sentence-transformers`, only keyword search is available.

## Troubleshooting

### "No embeddings" or slow search

Install sentence-transformers:
```bash
pip install sentence-transformers
```

### Files not indexed

Check file extensions (must be `.md`, `.markdown`, or `.txt`).

Check for ignore patterns in file names.

### Search returns no results

1. Verify files are indexed: `navig memory files`
2. Try re-indexing: `navig memory index --force`
3. Check query isn't too specific

### Memory bank too large

- Archive old files outside `~/.navig/memory/`
- Clear and rebuild: `navig memory clear-bank && navig memory index`


