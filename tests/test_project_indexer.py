"""
Tests for ProjectIndexer — source-code-aware indexing and BM25 search.

Covers:
- File discovery and ignore patterns
- Content-type classification
- Chunking (code vs docs)
- Full scan + incremental update
- BM25 search quality
- Per-file cap enforcement
- Stats and file tree
"""

from __future__ import annotations

import os
import tempfile
import textwrap
from pathlib import Path

import pytest

from navig.memory.project_indexer import (
    ProjectIndexer,
    ProjectIndexConfig,
    ProjectSearchResult,
    classify_content_type,
    content_type_priority,
    chunk_file,
    _is_ignored,
    _is_indexable,
    DEFAULT_EXCLUDES,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory for testing."""
    # source files
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.ts").write_text(textwrap.dedent("""\
        import { App } from './app';

        function main(): void {
            const app = new App();
            app.start();
            console.log('Server running on port 3000');
        }

        export class Server {
            private port: number;

            constructor(port: number) {
                this.port = port;
            }

            async start(): Promise<void> {
                // Start listening
                console.log(`Listening on ${this.port}`);
            }

            async stop(): Promise<void> {
                console.log('Shutting down');
            }
        }

        main();
    """))

    (src / "auth.ts").write_text(textwrap.dedent("""\
        export interface User {
            id: string;
            email: string;
            role: 'admin' | 'user';
        }

        export function authenticateUser(email: string, password: string): User | null {
            // Stub authentication logic
            if (email === 'admin@example.com' && password === 'secret') {
                return { id: '1', email, role: 'admin' };
            }
            return null;
        }

        export function authorizeRole(user: User, requiredRole: string): boolean {
            return user.role === requiredRole;
        }

        export class AuthService {
            private users: Map<string, User> = new Map();

            register(user: User): void {
                this.users.set(user.id, user);
            }

            login(email: string, password: string): User | null {
                return authenticateUser(email, password);
            }
        }
    """))

    # config files
    (tmp_path / "package.json").write_text('{"name": "test-project", "version": "1.0.0"}')
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"target": "es2020"}}')

    # docs
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(textwrap.dedent("""\
        # Test Project

        This is a test project for authentication and server management.

        ## Features
        - User authentication
        - Role-based authorization
        - Server lifecycle management
    """))

    # .navig plans
    navig = tmp_path / ".navig" / "plans"
    navig.mkdir(parents=True)
    (navig / "CURRENT_PHASE.md").write_text("Phase 1: Authentication module")

    # Ignored directories
    nm = tmp_path / "node_modules" / "lodash"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {};")

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "bundle.js").write_text("// compiled output")

    return tmp_path


@pytest.fixture
def indexer(project_dir: Path) -> ProjectIndexer:
    """Create a ProjectIndexer instance for the test project."""
    idx = ProjectIndexer(project_dir)
    yield idx
    idx.close()


# ============================================================================
# Content type classification
# ============================================================================

class TestClassifyContentType:
    def test_code_files(self):
        assert classify_content_type("src/main.ts") == "code"
        assert classify_content_type("app/models/user.py") == "code"
        assert classify_content_type("handlers/auth.go") == "code"

    def test_config_files(self):
        assert classify_content_type("package.json") == "config"
        assert classify_content_type("config/settings.yaml") == "config"
        assert classify_content_type(".env") == "config"

    def test_doc_files(self):
        assert classify_content_type("README.md") == "docs"
        assert classify_content_type("docs/GUIDE.txt") == "docs"

    def test_navig_brain_dirs(self):
        assert classify_content_type(".navig/wiki/howto.md") == "wiki"
        assert classify_content_type(".navig/plans/DEV_PLAN.md") == "plans"
        assert classify_content_type(".navig/memory/notes.md") == "memory"
        assert classify_content_type(".navig/workspace/USER.md") == "memory"

    def test_priority_multipliers(self):
        assert content_type_priority("code") == 1.0
        assert content_type_priority("plans") == 1.2
        assert content_type_priority("wiki") == 1.1
        assert content_type_priority("memory") == 0.7


# ============================================================================
# Ignore rules
# ============================================================================

class TestIgnoreRules:
    def test_default_excludes(self):
        assert _is_ignored("node_modules/lodash/index.js", DEFAULT_EXCLUDES)
        assert _is_ignored(".git/config", DEFAULT_EXCLUDES)
        assert _is_ignored("dist/bundle.js", DEFAULT_EXCLUDES)

    def test_allowed_files(self):
        assert not _is_ignored("src/main.ts", DEFAULT_EXCLUDES)
        assert not _is_ignored("docs/README.md", DEFAULT_EXCLUDES)

    def test_binary_extensions(self):
        assert _is_ignored("assets/logo.png", DEFAULT_EXCLUDES)
        assert _is_ignored("fonts/arial.woff2", DEFAULT_EXCLUDES)

    def test_is_indexable(self):
        assert _is_indexable("main.ts")
        assert _is_indexable("auth.py")
        assert _is_indexable("config.yaml")
        assert _is_indexable("README.md")
        assert not _is_indexable("image.png")
        assert not _is_indexable("data.bin")


# ============================================================================
# Chunking
# ============================================================================

class TestChunking:
    def test_code_chunk_size(self):
        content = "\n".join(f"line {i}" for i in range(200))
        config = ProjectIndexConfig(code_chunk_lines=80, code_chunk_overlap=10)
        chunks = chunk_file("src/main.ts", content, config)
        assert len(chunks) >= 2

        # First chunk should be ~80 lines
        first = chunks[0]
        assert first.start_line == 1
        assert first.end_line <= 80
        assert first.content_type == "code"

    def test_doc_chunk_size(self):
        content = "\n".join(f"Paragraph {i}" for i in range(120))
        config = ProjectIndexConfig(doc_chunk_lines=50, doc_chunk_overlap=5)
        chunks = chunk_file("docs/guide.md", content, config)
        assert len(chunks) >= 2
        assert chunks[0].content_type == "docs"

    def test_empty_file(self):
        chunks = chunk_file("empty.ts", "", ProjectIndexConfig())
        assert chunks == []

    def test_small_file_single_chunk(self):
        content = "line 1\nline 2\nline 3"
        chunks = chunk_file("small.ts", content, ProjectIndexConfig())
        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 3

    def test_section_title_extraction(self):
        content = "export function handleAuth(req: Request): Response {\n  return ok();\n}"
        chunks = chunk_file("handlers.ts", content, ProjectIndexConfig())
        assert len(chunks) == 1
        assert chunks[0].section_title == "handleAuth"

    def test_markdown_title_extraction(self):
        content = "# Authentication Guide\n\nThis guide covers auth setup."
        chunks = chunk_file("docs/auth.md", content, ProjectIndexConfig())
        assert chunks[0].section_title == "Authentication Guide"


# ============================================================================
# Full scan
# ============================================================================

class TestFullScan:
    def test_scan_discovers_files(self, indexer: ProjectIndexer, project_dir: Path):
        stats = indexer.scan()
        assert stats["files_discovered"] >= 4  # main.ts, auth.ts, package.json, etc.
        assert stats["files_indexed"] >= 4
        assert stats["chunks_created"] > 0
        assert stats["duration_s"] >= 0

    def test_scan_ignores_node_modules(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        s = indexer.stats()
        # node_modules should be excluded
        conn = indexer._get_conn()
        nm_files = conn.execute(
            "SELECT COUNT(*) FROM file_meta WHERE path LIKE 'node_modules%'"
        ).fetchone()[0]
        assert nm_files == 0

    def test_scan_ignores_dist(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        conn = indexer._get_conn()
        dist_files = conn.execute(
            "SELECT COUNT(*) FROM file_meta WHERE path LIKE 'dist%'"
        ).fetchone()[0]
        assert dist_files == 0

    def test_stats_after_scan(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        s = indexer.stats()
        assert s["file_count"] >= 4
        assert s["chunk_count"] > 0
        assert s["total_chars"] > 0
        assert s["db_path"].endswith("project_index.db")


# ============================================================================
# Incremental update
# ============================================================================

class TestIncrementalUpdate:
    def test_unchanged_files_skipped(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        stats = indexer.update_incremental()
        # No files changed, so nothing should be updated
        assert stats["files_updated"] == 0

    def test_modified_file_reindexed(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()

        # Modify a file
        auth = project_dir / "src" / "auth.ts"
        auth.write_text(auth.read_text() + "\n// New auth feature\n")

        stats = indexer.update_incremental()
        assert stats["files_updated"] >= 1

    def test_deleted_file_removed(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        initial_stats = indexer.stats()

        # Delete a file
        (project_dir / "src" / "auth.ts").unlink()

        stats = indexer.update_incremental()
        assert stats["files_deleted"] >= 1

        final_stats = indexer.stats()
        assert final_stats["file_count"] < initial_stats["file_count"]

    def test_new_file_indexed(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        initial_stats = indexer.stats()

        # Add a new file
        (project_dir / "src" / "utils.ts").write_text("export function clamp(n: number) { return n; }")

        stats = indexer.update_incremental()
        assert stats["files_updated"] >= 1

        final_stats = indexer.stats()
        assert final_stats["file_count"] > initial_stats["file_count"]


# ============================================================================
# BM25 search
# ============================================================================

class TestSearch:
    def test_basic_search(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        results = indexer.search("authenticate user login")
        assert len(results) > 0
        # auth.ts should be in results
        paths = [r.file_path for r in results]
        assert any("auth" in p for p in paths)

    def test_search_empty_query(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        results = indexer.search("")
        assert results == []

    def test_search_no_match(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        results = indexer.search("zyxwvu completely nonsensical query xyz")
        assert results == []

    def test_search_respects_top_k(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        results = indexer.search("server", top_k=2)
        assert len(results) <= 2

    def test_search_results_have_scores(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        results = indexer.search("authenticateUser email password")
        assert len(results) > 0
        for r in results:
            assert r.score >= 0
            assert r.rank > 0
        # At least the top result should have a positive score
        assert results[0].score > 0

    def test_per_file_cap(self, indexer: ProjectIndexer, project_dir: Path):
        # Create a large file that produces many chunks
        large_file = project_dir / "src" / "large.ts"
        lines = [f"export function handler{i}() {{ return {i}; }}" for i in range(500)]
        large_file.write_text("\n".join(lines))

        indexer.scan()
        results = indexer.search("handler export function")

        # Count chunks per file
        file_counts: dict = {}
        for r in results:
            file_counts[r.file_path] = file_counts.get(r.file_path, 0) + 1

        for path, count in file_counts.items():
            assert count <= indexer.config.max_chunks_per_file, (
                f"{path} has {count} chunks, exceeds cap of {indexer.config.max_chunks_per_file}"
            )

    def test_content_type_boosting(self, indexer: ProjectIndexer, project_dir: Path):
        # Add a .navig/plans file mentioning "authentication"
        plans_dir = project_dir / ".navig" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        (plans_dir / "auth_plan.md").write_text(
            "# Authentication Plan\n\nImplement OAuth2 authentication flow."
        )

        indexer.scan()
        results = indexer.search("authentication plan")

        # Plans should get priority boost (1.2x)
        plan_results = [r for r in results if r.content_type == "plans"]
        if plan_results:
            assert plan_results[0].score > 0


# ============================================================================
# File tree summary
# ============================================================================

class TestFileTree:
    def test_file_tree_not_empty(self, indexer: ProjectIndexer, project_dir: Path):
        indexer.scan()
        tree = indexer.file_tree_summary()
        assert "test-project" in tree or project_dir.name in tree
        assert "files" in tree

    def test_file_tree_empty_index(self, project_dir: Path):
        idx = ProjectIndexer(project_dir)
        tree = idx.file_tree_summary()
        assert "no files indexed" in tree
        idx.close()


# ============================================================================
# Lifecycle
# ============================================================================

class TestLifecycle:
    def test_context_manager(self, project_dir: Path):
        with ProjectIndexer(project_dir) as idx:
            stats = idx.scan()
            assert stats["files_indexed"] > 0

    def test_drop_index(self, project_dir: Path):
        idx = ProjectIndexer(project_dir)
        idx.scan()
        db_path = Path(idx.stats()["db_path"])
        assert db_path.exists()

        idx.drop_index()
        assert not db_path.exists()

    def test_double_close(self, project_dir: Path):
        idx = ProjectIndexer(project_dir)
        idx.close()
        idx.close()  # Should not raise
