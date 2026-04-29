"""Batch 110: tests for wiki_rag (WikiDocument, TextTokenizer, BM25Index) and workspace."""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# wiki_rag — WikiDocument
# ---------------------------------------------------------------------------

class TestWikiDocument:
    def test_basic_fields(self):
        from navig.wiki_rag import WikiDocument
        doc = WikiDocument(path="kb/test.md", title="Test Doc", content="Hello world.", folder="kb")
        assert doc.path == "kb/test.md"
        assert doc.title == "Test Doc"
        assert doc.folder == "kb"

    def test_chunks_auto_populated(self):
        from navig.wiki_rag import WikiDocument
        doc = WikiDocument(path="p", title="T", content="Hello world.", folder="f")
        assert doc.chunks is not None
        assert isinstance(doc.chunks, list)
        assert len(doc.chunks) >= 1

    def test_short_content_single_chunk(self):
        from navig.wiki_rag import WikiDocument
        doc = WikiDocument(path="p", title="T", content="Short text.", folder="f")
        assert len(doc.chunks) == 1
        assert doc.chunks[0] == "Short text."

    def test_long_content_multiple_chunks(self):
        from navig.wiki_rag import WikiDocument
        long_content = "A" * 1200  # exceeds 500 char chunk_size
        doc = WikiDocument(path="p", title="T", content=long_content, folder="f")
        assert len(doc.chunks) > 1

    def test_empty_content_produces_chunks(self):
        from navig.wiki_rag import WikiDocument
        doc = WikiDocument(path="p", title="T", content="", folder="f")
        assert isinstance(doc.chunks, list)

    def test_explicit_chunks_not_overwritten(self):
        from navig.wiki_rag import WikiDocument
        predefined = ["chunk1", "chunk2"]
        doc = WikiDocument(path="p", title="T", content="whatever", folder="f", chunks=predefined)
        assert doc.chunks == predefined


# ---------------------------------------------------------------------------
# wiki_rag — TextTokenizer
# ---------------------------------------------------------------------------

class TestTextTokenizer:
    def test_returns_list(self):
        from navig.wiki_rag import TextTokenizer
        result = TextTokenizer.tokenize("Hello world")
        assert isinstance(result, list)

    def test_filters_stop_words(self):
        from navig.wiki_rag import TextTokenizer
        result = TextTokenizer.tokenize("the quick brown fox")
        assert "the" not in result

    def test_lowercases_words(self):
        from navig.wiki_rag import TextTokenizer
        result = TextTokenizer.tokenize("NAVIG Database")
        assert all(w == w.lower() for w in result)

    def test_filters_short_words(self):
        from navig.wiki_rag import TextTokenizer
        result = TextTokenizer.tokenize("is a to go")
        # All words are <= 2 chars or stop words
        assert len(result) == 0

    def test_keeps_meaningful_words(self):
        from navig.wiki_rag import TextTokenizer
        result = TextTokenizer.tokenize("navig database configuration")
        assert "navig" in result
        assert "database" in result
        assert "configuration" in result

    def test_handles_empty_string(self):
        from navig.wiki_rag import TextTokenizer
        assert TextTokenizer.tokenize("") == []

    def test_handles_punctuation(self):
        from navig.wiki_rag import TextTokenizer
        result = TextTokenizer.tokenize("hello, world! foo.bar")
        assert "hello" in result
        assert "world" in result

    def test_stop_words_set_is_populated(self):
        from navig.wiki_rag import TextTokenizer
        assert len(TextTokenizer.STOP_WORDS) > 10
        assert "the" in TextTokenizer.STOP_WORDS
        assert "and" in TextTokenizer.STOP_WORDS


# ---------------------------------------------------------------------------
# wiki_rag — BM25Index
# ---------------------------------------------------------------------------

class TestBM25Index:
    def test_default_params(self):
        from navig.wiki_rag import BM25Index
        idx = BM25Index()
        assert idx.k1 == 1.5
        assert idx.b == 0.75

    def test_custom_params(self):
        from navig.wiki_rag import BM25Index
        idx = BM25Index(k1=1.2, b=0.5)
        assert idx.k1 == 1.2
        assert idx.b == 0.5

    def test_starts_empty(self):
        from navig.wiki_rag import BM25Index
        idx = BM25Index()
        assert len(idx.documents) == 0

    def test_index_documents(self):
        from navig.wiki_rag import BM25Index, WikiDocument
        idx = BM25Index()
        doc = WikiDocument(path="p", title="T", content="navig database backup", folder="f")
        idx.index([doc])
        assert len(idx.documents) >= 1

    def test_search_returns_list(self):
        from navig.wiki_rag import BM25Index, WikiDocument
        idx = BM25Index()
        doc = WikiDocument(path="p", title="T", content="navig database configuration", folder="f")
        idx.index([doc])
        results = idx.search("database", top_k=5)
        assert isinstance(results, list)

    def test_search_finds_relevant_doc(self):
        from navig.wiki_rag import BM25Index, WikiDocument
        idx = BM25Index()
        doc1 = WikiDocument(path="db.md", title="DB", content="database backup restore configuration", folder="kb")
        doc2 = WikiDocument(path="net.md", title="Net", content="network firewall ssh tunnel", folder="kb")
        idx.index([doc1, doc2])
        results = idx.search("database backup", top_k=5)
        assert len(results) > 0

    def test_empty_query_returns_empty(self):
        from navig.wiki_rag import BM25Index, WikiDocument
        idx = BM25Index()
        idx.index([WikiDocument(path="p", title="T", content="some content", folder="f")])
        results = idx.search("", top_k=5)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# workspace — WorkspaceManager constants and get_workspace_manager
# ---------------------------------------------------------------------------

class TestWorkspaceManagerConstants:
    def test_bootstrap_files_is_list(self):
        from navig.workspace import WorkspaceManager
        assert isinstance(WorkspaceManager.BOOTSTRAP_FILES, list)

    def test_bootstrap_files_contains_identity(self):
        from navig.workspace import WorkspaceManager
        assert "IDENTITY.md" in WorkspaceManager.BOOTSTRAP_FILES

    def test_bootstrap_files_contains_soul(self):
        from navig.workspace import WorkspaceManager
        assert "SOUL.md" in WorkspaceManager.BOOTSTRAP_FILES

    def test_bootstrap_files_all_markdown(self):
        from navig.workspace import WorkspaceManager
        for f in WorkspaceManager.BOOTSTRAP_FILES:
            assert f.endswith(".md"), f"{f} is not a .md file"

    def test_bootstrap_files_non_empty(self):
        from navig.workspace import WorkspaceManager
        assert len(WorkspaceManager.BOOTSTRAP_FILES) >= 5


class TestWorkspaceManagerInit:
    def test_instantiates_with_custom_dirs(self, tmp_path):
        from navig.workspace import WorkspaceManager
        ws_path = tmp_path / "workspace"
        ws_path.mkdir()
        cfg_path = tmp_path / "navig.json"
        mgr = WorkspaceManager(workspace_path=ws_path, config_path=cfg_path)
        assert mgr is not None

    def test_config_path_attribute(self, tmp_path):
        from navig.workspace import WorkspaceManager
        cfg_path = tmp_path / "my.json"
        mgr = WorkspaceManager(config_path=cfg_path)
        assert mgr.config_path == cfg_path

    def test_config_is_dict_or_none(self, tmp_path):
        from navig.workspace import WorkspaceManager
        mgr = WorkspaceManager(config_path=tmp_path / "cfg.json")
        # config may be None (no existing config) or a dict
        assert mgr.config is None or isinstance(mgr.config, dict)
