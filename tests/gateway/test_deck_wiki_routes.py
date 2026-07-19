"""Contract tests for the /api/deck/wiki + /api/deck/memory/query routes (B4).

Exercises the wiki browser (list/read/write/search over the ``navig wiki``
engine) and the unified memory query (plans + git log + wiki index + notes
within a char budget) over real HTTP (TestClient) against a temp space
fixture: listing with hidden-dir filtering, page read/write + traversal
confinement, engine-backed search, budget truncation, per-source query
filtering, and space resolution.

All filesystem state is isolated: NAVIG_DATA_DIR → tmp_path, the fixture
space carries its own ``.navig/wiki`` tree, and git tests init a private
repo inside the fixture (local user.name/email, no gpg).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.gateway.deck.routes.wiki as wiki_routes

_HAS_GIT = shutil.which("git") is not None

GUIDE_MD = """# Deploy Guide

How to deploy the beacon service safely.

Always dry-run first.
"""

CONCEPT_MD = """# Beacon Concept

A beacon is the heartbeat emitter.
"""

NO_HEADING_MD = """just a stub page without a heading
"""

PHASE_MD = """---
phase: 04
title: Beacon Rollout
status: active
---

## Objective

Ship the beacon rollout across spaces.

## Active Tasks

- Wire the beacon routes
"""

DEV_PLAN_MD = """# DEV Plan

- [x] Design the beacon
- [ ] Ship the beacon
"""


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/wiki/pages", wiki_routes.handle_wiki_pages)
    app.router.add_get("/api/deck/wiki/page", wiki_routes.handle_wiki_page_get)
    app.router.add_post("/api/deck/wiki/page", wiki_routes.handle_wiki_page_save)
    app.router.add_get("/api/deck/wiki/search", wiki_routes.handle_wiki_search)
    app.router.add_get("/api/deck/memory/query", wiki_routes.handle_memory_query)
    return app


@pytest.fixture()
def space(tmp_path, monkeypatch):
    """A temp space with a populated .navig/wiki tree; cwd bound to its root.

    The .navig/ marker stops the project-root walk-up at the fixture — without
    it the walk would escape tmp and resolve to the real repo's .navig.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "space"
    wiki = root / ".navig" / "wiki"
    (wiki / "knowledge" / "guides").mkdir(parents=True)
    (wiki / "technical").mkdir()
    (wiki / ".meta").mkdir()
    (wiki / "knowledge" / "guides" / "deploy.md").write_text(GUIDE_MD, encoding="utf-8")
    (wiki / "knowledge" / "concept.md").write_text(CONCEPT_MD, encoding="utf-8")
    (wiki / "technical" / "stub.md").write_text(NO_HEADING_MD, encoding="utf-8")
    (wiki / ".meta" / "index.md").write_text("# Hidden Index\n", encoding="utf-8")
    plans = root / ".navig" / "plans"
    plans.mkdir(parents=True)
    (plans / "CURRENT_PHASE.md").write_text(PHASE_MD, encoding="utf-8")
    (plans / "DEV_PLAN.md").write_text(DEV_PLAN_MD, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _git(root, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8"
    )


def _init_repo(root) -> None:
    assert _git(root, "init", "-q").returncode == 0
    _git(root, "config", "user.email", "test@navig.test")
    _git(root, "config", "user.name", "navig-test")
    _git(root, "config", "commit.gpgsign", "false")
    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-q", "-m", "init the beacon space").returncode == 0


async def _post(client: TestClient, path: str, body: dict):
    res = await client.post(path, json=body)
    return res.status, await res.json()


# ── Pages listing ─────────────────────────────────────────────────────────────


async def test_pages_listing_happy_path(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/wiki/pages")
        assert res.status == 200
        body = await res.json()

    assert body["ok"] is True
    data = body["data"]
    assert data["exists"] is True

    pages = {p["path"]: p for p in data["pages"]}
    assert set(pages) == {
        "knowledge/guides/deploy.md",
        "knowledge/concept.md",
        "technical/stub.md",
    }
    # Hidden dirs (.meta) never appear in the listing.
    assert not any(p.startswith(".") for p in pages)

    deploy = pages["knowledge/guides/deploy.md"]
    assert deploy["title"] == "Deploy Guide"  # first heading wins
    assert deploy["name"] == "deploy"
    assert deploy["folder"] == "knowledge/guides"
    assert deploy["size"] > 0 and deploy["mtime"] > 0
    # No heading → the stem is the title.
    assert pages["technical/stub.md"]["title"] == "stub"


async def test_pages_listing_without_wiki_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    bare = tmp_path / "bare"
    (bare / ".navig").mkdir(parents=True)  # marker only — no wiki dir
    monkeypatch.chdir(bare)

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/wiki/pages")
        assert res.status == 200
        body = await res.json()

    assert body["data"] == {"exists": False, "pages": []}


async def test_unknown_space_is_404(space, monkeypatch):
    monkeypatch.setattr(wiki_routes, "_space_root", lambda s: None)
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/wiki/pages?space=ghost")
        assert res.status == 404
        body = await res.json()
    assert body["ok"] is False and "ghost" in body["error"]


async def test_space_id_resolves_to_its_root(space, tmp_path, monkeypatch):
    # cwd sits in a DIFFERENT project; ?space= must still hit the fixture space.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".navig").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(
        wiki_routes, "_space_root", lambda s: space if s == "myspace" else None
    )

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/wiki/pages?space=myspace")
        assert res.status == 200
        body = await res.json()
    assert any(p["path"] == "knowledge/concept.md" for p in body["data"]["pages"])


# ── Page read / write ─────────────────────────────────────────────────────────


async def test_page_read_write_roundtrip(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/wiki/page?path=knowledge/guides/deploy.md")
        assert res.status == 200
        data = (await res.json())["data"]
        assert data["path"] == "knowledge/guides/deploy.md"
        assert data["content"] == GUIDE_MD
        assert data["mtime"] > 0

        # Create a NEW page in a fresh subfolder, then read it back.
        status, body = await _post(
            client, "/api/deck/wiki/page",
            {"path": "hub/notes/new-note.md", "content": "# New Note\n\nBody.\n"},
        )
        assert status == 200 and body["ok"] is True
        assert body["data"]["path"] == "hub/notes/new-note.md"
        assert body["data"]["created"] is True

        res = await client.get("/api/deck/wiki/page?path=hub/notes/new-note.md")
        assert res.status == 200
        assert (await res.json())["data"]["content"] == "# New Note\n\nBody.\n"

        # Overwrite an EXISTING page → created:false, content replaced.
        status, body = await _post(
            client, "/api/deck/wiki/page",
            {"path": "knowledge/concept.md", "content": "# Beacon Concept v2\n"},
        )
        assert status == 200 and body["data"]["created"] is False

    assert (space / ".navig" / "wiki" / "hub" / "notes" / "new-note.md").is_file()
    on_disk = (space / ".navig" / "wiki" / "knowledge" / "concept.md").read_text(
        encoding="utf-8"
    )
    assert on_disk == "# Beacon Concept v2\n"


async def test_page_traversal_and_bad_paths_rejected(space):
    secret = space / "secrets.md"
    secret.write_text("nope", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        for bad in ("../../secrets.md", "..\\..\\secrets.md", "/etc/passwd.md", ""):
            res = await client.get("/api/deck/wiki/page", params={"path": bad})
            assert res.status == 400, bad

        status, _ = await _post(
            client, "/api/deck/wiki/page", {"path": "../../escape.md", "content": "x"}
        )
        assert status == 400
        status, body = await _post(
            client, "/api/deck/wiki/page", {"path": "evil.txt", "content": "x"}
        )
        assert status == 400 and ".md" in body["error"]
        # Hidden wiki internals are not writable through the editor surface.
        status, body = await _post(
            client, "/api/deck/wiki/page", {"path": ".meta/index.md", "content": "x"}
        )
        assert status == 400 and "hidden" in body["error"]
        # content must be a string
        status, _ = await _post(client, "/api/deck/wiki/page", {"path": "a.md"})
        assert status == 400

        res = await client.get("/api/deck/wiki/page?path=missing.md")
        assert res.status == 404

    assert not (space / "escape.md").exists()
    assert secret.read_text(encoding="utf-8") == "nope"


# ── Search ────────────────────────────────────────────────────────────────────


async def test_search_uses_wiki_engine(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/wiki/search?q=beacon")
        assert res.status == 200
        data = (await res.json())["data"]

        assert data["q"] == "beacon"
        paths = {r["path"] for r in data["results"]}
        assert paths == {"knowledge/concept.md", "knowledge/guides/deploy.md"}
        for r in data["results"]:
            assert r["matches"] >= 1
            assert "beacon" in r["context"].lower()

        # No hits → empty results, not an error.
        res = await client.get("/api/deck/wiki/search?q=zzz-not-here")
        assert res.status == 200
        assert (await res.json())["data"]["results"] == []

        # Empty query is a 400.
        res = await client.get("/api/deck/wiki/search?q=")
        assert res.status == 400


# ── Unified memory query ──────────────────────────────────────────────────────


async def test_memory_query_snapshot_sections(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/memory/query")
        assert res.status == 200
        data = (await res.json())["data"]

    assert data["q"] == ""
    assert data["truncated"] is False
    assert 0 < data["used"] <= data["budget"]

    by_source = {s["source"]: s for s in data["sections"]}
    # No git repo in the fixture → no git section; notes store is empty.
    assert "plans" in by_source and "wiki" in by_source
    assert "git" not in by_source

    plans = by_source["plans"]["content"]
    assert "CURRENT_PHASE.md" in plans and "Beacon Rollout" in plans
    assert "DEV_PLAN.md" in plans

    wiki = by_source["wiki"]["content"]
    # First-heading excerpts with rel paths; hidden pages never leak.
    assert "- Deploy Guide (knowledge/guides/deploy.md)" in wiki
    assert ".meta" not in wiki
    for s in data["sections"]:
        assert s["truncated"] is False
        assert s["title"]


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
async def test_memory_query_includes_git_log(space):
    _init_repo(space)
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/memory/query")
        assert res.status == 200
        data = (await res.json())["data"]

    by_source = {s["source"]: s for s in data["sections"]}
    assert "init the beacon space" in by_source["git"]["content"]


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
async def test_memory_query_q_filters_every_source(space):
    _init_repo(space)
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/memory/query?q=beacon")
        assert res.status == 200
        data = (await res.json())["data"]

    assert data["q"] == "beacon"
    by_source = {s["source"]: s for s in data["sections"]}

    # plans: only lines mentioning the query survive.
    plans = by_source["plans"]["content"]
    assert "beacon" in plans.lower()
    assert "## Objective" not in plans

    # git: subject filter.
    assert "init the beacon space" in by_source["git"]["content"]

    # wiki: engine search hits (path + match context), not the index.
    wiki = by_source["wiki"]["content"]
    assert by_source["wiki"]["title"] == "Wiki matches"
    assert "knowledge/concept.md" in wiki

    # A query with no hits anywhere → no sections, not an error.
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/memory/query?q=zzz-not-here")
        assert res.status == 200
        assert (await res.json())["data"]["sections"] == []


async def test_memory_query_budget_truncates(space):
    # Grow the phase doc past any 600-char budget (adapter caps at 80 lines).
    phase = space / ".navig" / "plans" / "CURRENT_PHASE.md"
    filler = "\n".join(f"- Task {i}: polish the beacon rollout step {i}" for i in range(60))
    phase.write_text(PHASE_MD + "\n" + filler + "\n", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/memory/query?budget=600")
        assert res.status == 200
        data = (await res.json())["data"]

        assert data["budget"] == 600  # min-clamped values pass through
        assert data["truncated"] is True
        assert data["used"] <= 600
        # The crossing section is marked; later sections are dropped.
        assert data["sections"], "at least the first section must survive"
        assert data["sections"][-1]["truncated"] is True

        # Non-integer budget is a 400; tiny budgets clamp to the minimum.
        res = await client.get("/api/deck/memory/query?budget=lots")
        assert res.status == 400
        res = await client.get("/api/deck/memory/query?budget=1")
        assert res.status == 200
        assert (await res.json())["data"]["budget"] == 500


async def test_memory_query_notes_from_key_fact_store(space, tmp_path):
    from navig.memory.key_facts import (
        KeyFact,
        KeyFactStore,
        get_key_fact_store,
        reset_key_fact_store,
    )

    # Point the singleton at a private db for this test, then always reset it
    # so other tests (any NAVIG_DATA_DIR) never see this store.
    reset_key_fact_store()
    store = get_key_fact_store(db_path=tmp_path / "facts" / "key_facts.db")
    assert isinstance(store, KeyFactStore)
    try:
        store.upsert(KeyFact(content="Operator prefers beacon dry-runs", category="preference"))

        async with TestClient(TestServer(_app())) as client:
            res = await client.get("/api/deck/memory/query")
            assert res.status == 200
            data = (await res.json())["data"]
            by_source = {s["source"]: s for s in data["sections"]}
            assert "- [preference] Operator prefers beacon dry-runs" in (
                by_source["notes"]["content"]
            )

            # And the query path filters facts too.
            res = await client.get("/api/deck/memory/query?q=dry-runs")
            assert res.status == 200
            data = (await res.json())["data"]
            by_source = {s["source"]: s for s in data["sections"]}
            assert "notes" in by_source
    finally:
        reset_key_fact_store()
