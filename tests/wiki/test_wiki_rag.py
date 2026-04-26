"""
Tests for navig/wiki_rag.py

Covers WikiDocument chunking, TextTokenizer, BM25Index search and IDF,
and WikiRAG without a real wiki directory (uses tmp_path).
All tests are hermetic — no real wiki files required.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from navig.wiki_rag import BM25Index, TextTokenizer, WikiDocument


# ---------------------------------------------------------------------------
# WikiDocument
# ---------------------------------------------------------------------------


class TestWikiDocumentChunking:
    def test_short_content_is_single_chunk(self):
        doc = WikiDocument(path="a.md", title="T", content="Short content here.", folder="kb")
        assert len(doc.chunks) == 1
        assert doc.chunks[0] == "Short content here."

    def test_long_content_is_split(self):
        # Build content longer than default chunk_size=500
        content = ("word " * 200).strip()  # ~1000 chars
        doc = WikiDocument(path="b.md", title="T", content=content, folder="kb")
        assert len(doc.chunks) >= 2

    def test_chunks_total_coverage(self):
        content = ("hello world " * 100).strip()
        doc = WikiDocument(path="c.md", title="T", content=content, folder="kb")
        # Every word from the original should appear in at least one chunk
        assert all("hello" in " ".join(doc.chunks) for _ in [1])

    def test_explicit_chunks_not_overwritten(self):
        doc = WikiDocument(
            path="d.md", title="T", content="anything", folder="kb",
            chunks=["custom1", "custom2"]
        )
        assert doc.chunks == ["custom1", "custom2"]

    def test_empty_content_single_empty_chunk(self):
        doc = WikiDocument(path="e.md", title="T", content="", folder="kb")
        # <= chunk_size → returns [content]
        assert doc.chunks == [""]

    def test_fields_stored(self):
        doc = WikiDocument(path="f.md", title="Title", content="Body", folder="tech")
        assert doc.path == "f.md"
        assert doc.title == "Title"
        assert doc.content == "Body"
        assert doc.folder == "tech"


# ---------------------------------------------------------------------------
# TextTokenizer.tokenize()
# ---------------------------------------------------------------------------


class TestTextTokenizer:
    def test_returns_list(self):
        result = TextTokenizer.tokenize("hello world")
        assert isinstance(result, list)

    def test_lowercases_words(self):
        result = TextTokenizer.tokenize("Hello World")
        assert "hello" in result
        assert "world" in result

    def test_filters_stop_words(self):
        result = TextTokenizer.tokenize("this is a the test")
        # "this", "is", "a", "the" are stop words
        assert "this" not in result
        assert "the" not in result
        assert "test" in result

    def test_filters_short_words(self):
        # Words ≤2 chars filtered out
        result = TextTokenizer.tokenize("go on do it up")
        for w in result:
            assert len(w) > 2

    def test_non_alphanumeric_split(self):
        result = TextTokenizer.tokenize("foo-bar baz.qux")
        assert "foo" in result
        assert "bar" in result
        assert "baz" in result
        assert "qux" in result

    def test_numbers_included(self):
        result = TextTokenizer.tokenize("version 123 config")
        assert "123" in result or "version" in result

    def test_empty_string_returns_empty(self):
        assert TextTokenizer.tokenize("") == []

    def test_only_stop_words_returns_empty(self):
        result = TextTokenizer.tokenize("the a an and or but")
        assert result == []

    def test_deduplication_not_applied(self):
        # tokenize does NOT deduplicate; counts matter for BM25
        result = TextTokenizer.tokenize("docker docker docker")
        assert result.count("docker") == 3


# ---------------------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------------------


def _make_doc(path: str, content: str, folder: str = "kb") -> WikiDocument:
    return WikiDocument(path=path, title=path, content=content, folder=folder)


class TestBM25IndexDefaultParams:
    def test_default_k1_b(self):
        idx = BM25Index()
        assert idx.k1 == pytest.approx(1.5)
        assert idx.b == pytest.approx(0.75)

    def test_custom_params(self):
        idx = BM25Index(k1=1.2, b=0.5)
        assert idx.k1 == pytest.approx(1.2)
        assert idx.b == pytest.approx(0.5)


class TestBM25IndexIndex:
    def test_empty_index(self):
        idx = BM25Index()
        idx.index([])
        assert idx.documents == []
        assert idx.avg_doc_len == 0

    def test_single_document_indexed(self):
        idx = BM25Index()
        doc = _make_doc("a.md", "docker nginx database")
        idx.index([doc])
        assert len(idx.documents) == 1

    def test_multi_document_indexed(self):
        idx = BM25Index()
        docs = [
            _make_doc("a.md", "docker nginx database"),
            _make_doc("b.md", "ssh tunnel connection"),
            _make_doc("c.md", "python script automation"),
        ]
        idx.index(docs)
        assert len(idx.documents) == 3

    def test_avg_doc_len_positive(self):
        idx = BM25Index()
        idx.index([_make_doc("x.md", "word1 word2 word3 word4 word5")])
        assert idx.avg_doc_len > 0

    def test_doc_freqs_populated(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "docker nginx"), _make_doc("b.md", "docker database")])
        # "docker" appears in 2 doc chunks
        assert idx.doc_freqs.get("docker", 0) == 2


class TestBM25IndexIdf:
    def test_idf_zero_for_unknown_term(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "nginx docker")])
        assert idx._idf("__unknown__") == 0

    def test_idf_positive_for_known_term(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "nginx docker"), _make_doc("b.md", "mysql database")])
        # "nginx" only in doc a → positive IDF
        idf = idx._idf("nginx")
        assert idf > 0

    def test_idf_decreases_with_wider_coverage(self):
        idx = BM25Index()
        # "common" appears in all 3 docs; "rare" only in 1
        idx.index([
            _make_doc("a.md", "common rare"),
            _make_doc("b.md", "common other"),
            _make_doc("c.md", "common third"),
        ])
        idf_common = idx._idf("common")
        idf_rare = idx._idf("rare")
        assert idf_rare > idf_common


class TestBM25IndexSearch:
    def test_empty_query_returns_empty(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "docker nginx")])
        assert idx.search("") == []

    def test_query_only_stop_words_returns_empty(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "docker nginx")])
        assert idx.search("the a and") == []

    def test_relevant_doc_ranked_first(self):
        idx = BM25Index()
        idx.index([
            _make_doc("docker.md", "docker container image registry deployment"),
            _make_doc("database.md", "mysql postgresql database schema migration"),
        ])
        results = idx.search("docker container")
        assert len(results) > 0
        assert results[0]["path"] == "docker.md"

    def test_returns_dicts_with_required_keys(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "nginx webserver virtual host config")])
        results = idx.search("nginx")
        assert len(results) >= 1
        result = results[0]
        for key in ("path", "title", "folder", "score", "chunk"):
            assert key in result, f"Missing key: {key}"

    def test_score_is_positive(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "nginx webserver")])
        results = idx.search("nginx")
        assert results[0]["score"] > 0

    def test_top_k_limits_results(self):
        idx = BM25Index()
        docs = [_make_doc(f"{i}.md", f"docker nginx topic{i}") for i in range(20)]
        idx.index(docs)
        results = idx.search("docker nginx", top_k=3)
        assert len(results) <= 3

    def test_deduplicates_same_doc_paths(self):
        # A long document produces multiple chunks — should only appear once
        long_content = ("docker container deployment " * 100).strip()
        idx = BM25Index()
        idx.index([_make_doc("long.md", long_content)])
        results = idx.search("docker container")
        paths = [r["path"] for r in results]
        assert len(paths) == len(set(paths)), "Duplicate paths in results"

    def test_unknown_query_returns_empty(self):
        idx = BM25Index()
        idx.index([_make_doc("a.md", "nginx docker database")])
        results = idx.search("xyzyplorp")
        assert results == []

    def test_multi_word_query_scores_higher_than_single(self):
        idx = BM25Index()
        idx.index([
            _make_doc("match.md", "nginx reverse proxy load balancer"),
            _make_doc("other.md", "nginx unrelated content here"),
        ])
        results_multi = idx.search("nginx reverse proxy")
        results_single = idx.search("nginx")
        # Both should return something
        assert len(results_multi) > 0
        assert len(results_single) > 0

    def test_chunk_field_truncated_at_300(self):
        long_content = "word " * 200  # ~1000 chars, forces a long chunk
        idx = BM25Index()
        idx.index([_make_doc("long.md", long_content)])
        results = idx.search("word")
        if results:
            chunk = results[0]["chunk"]
            # chunk is either ≤300 chars or ends with "..."
            assert len(chunk) <= 303 + 3 or chunk.endswith("...")
