"""Prompt library v3: capture provenance (project, source_url, content_hash), the additive
migration from older libraries, and ``POST /api/deck/prompts/batch`` dedupe by content."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from navig.store.prompt_library import PromptLibraryStore, content_hash, get_prompt_store

V2_SCHEMA = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES ({ver});
CREATE TABLE prompts (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
    use_when TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    collection TEXT NOT NULL DEFAULT '', step INTEGER, tags_json TEXT NOT NULL DEFAULT '[]',
    variables_json TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'user',
    source_path TEXT NOT NULL DEFAULT '', source_hash TEXT NOT NULL DEFAULT '',
    user_edited INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0
);
INSERT INTO prompts (id, title, body, created_at, updated_at, favorite, use_count)
VALUES ('dev/audit', 'Audit', 'You are an auditor.\n\n  Review [REPO].', '2026-01-01', '2026-01-01', 1, 7);
"""


def _old_library(path: Path, version: int) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(V2_SCHEMA.format(ver=version))
    conn.commit()
    conn.close()


def test_content_hash_ignores_whitespace_layout_only():
    assert content_hash("a  b\n\nc ") == content_hash(" a b c")
    assert content_hash("a b c") != content_hash("A b c")
    assert len(content_hash("x")) == 64


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_library_migrates_additively_and_backfills_hashes(tmp_path: Path, version: int):
    db = tmp_path / "prompts.db"
    _old_library(db, version)
    store = PromptLibraryStore(db)
    assert store.get_schema_version() == PromptLibraryStore.SCHEMA_VERSION
    rec = store.get("dev/audit")
    assert rec is not None and rec.favorite and rec.use_count == 7  # nothing the operator had is lost
    assert rec.content_hash == content_hash("You are an auditor. Review [REPO].")
    assert rec.project == "" and rec.source_url == ""
    assert store.find_by_hash(rec.content_hash).id == "dev/audit"
    assert (tmp_path / f"prompts.v{version}-backup.db").exists()
    idx = {r[1] for r in store._read_all("PRAGMA index_list(prompts)")}
    assert "idx_prompts_hash" in idx
    store.close()
    again = PromptLibraryStore(db)  # re-opening is a no-op
    assert again.get_schema_version() == PromptLibraryStore.SCHEMA_VERSION
    assert again.count() == 1
    again.close()


def test_new_prompts_carry_project_source_url_and_hash(tmp_path: Path):
    store = PromptLibraryStore(tmp_path / "p.db")
    rec = store.create(title="Shard", body="Explain  sharding.", project="navig",
                       source_url="https://chatgpt.com/c/1", source="capture")
    got = store.get(rec.id)
    assert (got.project, got.source_url, got.source) == ("navig", "https://chatgpt.com/c/1", "capture")
    assert got.content_hash == content_hash("Explain sharding.")
    assert [r.id for r in store.list(project="navig")] == [rec.id]
    assert store.list(project="other") == []
    store.update(rec.id, {"body": "Explain partitioning."})
    assert store.get(rec.id).content_hash == content_hash("Explain partitioning.")
    store.close()


# ── POST /api/deck/prompts/batch ─────────────────────────────


class _Req:
    def __init__(self, body=None, query=None):
        self._body = body
        self.query = query or {}
        self.app = {}

    async def json(self):
        return self._body


def _b(resp):
    return resp.status, json.loads(resp.body.decode())


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    s = get_prompt_store()
    assert Path(s.db_path).resolve().is_relative_to(tmp_path.resolve())
    return s


@pytest.mark.asyncio
async def test_batch_creates_then_reports_duplicates_by_content(lib):
    from navig.gateway.deck.routes import prompts as r

    items = [
        {"title": "Role prompt", "body": "You are a reviewer.\nReview {file}.", "tags": ["chatgpt"],
         "project": "navig", "source_url": "https://chatgpt.com/c/1"},
        {"title": "Second", "body": "Summarise [TOPIC] in three bullets.", "project": "navig",
         "source_url": "https://chatgpt.com/c/1"},
        {"title": "Same as first, other spacing", "body": "You are a reviewer.   Review {file}."},
    ]
    status, body = _b(await r.handle_batch(_Req({"prompts": items})))
    assert status == 200 and body["ok"]
    res = body["data"]["results"]
    assert [x["status"] for x in res] == ["created", "created", "duplicate"]
    assert res[2]["id"] == res[0]["id"]  # deduped within the batch
    assert (body["data"]["created"], body["data"]["duplicate"], body["data"]["error"]) == (2, 1, 0)
    stored = lib.get(res[0]["id"])
    assert stored.project == "navig" and stored.source_url == "https://chatgpt.com/c/1" and "chatgpt" in stored.tags

    status, body = _b(await r.handle_batch(_Req({"prompts": items[:2]})))
    assert [x["status"] for x in body["data"]["results"]] == ["duplicate", "duplicate"]
    assert lib.count() == 2

    status, body = _b(await r.handle_list(_Req(query={"project": "navig"})))
    assert status == 200 and len(body["data"]) == 2
    assert {p["source_url"] for p in body["data"]} == {"https://chatgpt.com/c/1"}


@pytest.mark.asyncio
async def test_batch_reports_bad_items_without_failing_the_rest(lib):
    from navig.gateway.deck.routes import prompts as r

    status, body = _b(await r.handle_batch(_Req({"prompts": [
        {"title": "empty", "body": "   "}, "not an object",
        {"body": "Fine prompt body", "source_url": "javascript:alert(1)"},
        {"body": "Untitled but fine prompt body"},
    ]})))
    assert status == 200
    res = body["data"]["results"]
    assert [x["status"] for x in res] == ["error", "error", "error", "created"]
    assert lib.get(res[3]["id"]).title.startswith("Untitled but fine")


@pytest.mark.asyncio
async def test_batch_refuses_bad_envelopes(lib):
    from navig.gateway.deck.routes import prompts as r

    assert _b(await r.handle_batch(_Req(None)))[0] == 400
    assert _b(await r.handle_batch(_Req({"prompts": []})))[0] == 400
    assert _b(await r.handle_batch(_Req({"prompts": [{"body": "x"}] * (r.BATCH_MAX + 1)})))[0] == 413
