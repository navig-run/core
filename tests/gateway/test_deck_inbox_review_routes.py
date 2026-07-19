"""Contract tests for the /api/deck/inbox/review routes (review queue + sandbox).

Exercises the plans-scoped inbox lifecycle over real HTTP (TestClient) against
a temp space fixture: state listing + filters, item detail, sandbox analysis
(no side effects), approve (routed copy + ``.md.done``, confined targets),
reject (``.md.archive``), requeue (undo), and space resolution.

All filesystem state is isolated: NAVIG_DATA_DIR → tmp_path and the fixture
space carries its own ``.navig/inbox`` tree.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.gateway.deck.routes.inbox_review as review_routes

TASK_MD = """---
title: Wire the deck routes
type: task
---

Add the review queue task so the OS app can approve items.
"""

REVIEW_MD = """---
title: Uncertain idea
review_reason: Potential duplicate of 'older idea'
---

This one needs a human decision before it goes anywhere.
"""

DONE_MD = """---
title: Shipped note
---

Already approved earlier.
"""

ARCHIVE_MD = """---
title: Parked note
---

Rejected earlier.
"""

DECISION_MD = """---
title: Choose the storage engine
type: decision
---

We decided to keep SQLite behind the storage engine.
"""


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/inbox/review", review_routes.handle_inbox_review_list)
    app.router.add_get("/api/deck/inbox/review/item", review_routes.handle_inbox_review_item)
    app.router.add_post("/api/deck/inbox/review/analyse", review_routes.handle_inbox_review_analyse)
    app.router.add_post("/api/deck/inbox/review/approve", review_routes.handle_inbox_review_approve)
    app.router.add_post("/api/deck/inbox/review/reject", review_routes.handle_inbox_review_reject)
    app.router.add_post("/api/deck/inbox/review/requeue", review_routes.handle_inbox_review_requeue)
    return app


@pytest.fixture()
def space(tmp_path, monkeypatch):
    """A temp space with a populated .navig/inbox; cwd bound to its root.

    The .navig/ marker stops the project-root walk-up at the fixture — without
    it the walk would escape tmp and resolve to the real repo's .navig.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "space"
    inbox = root / ".navig" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "wire_routes.md").write_text(TASK_MD, encoding="utf-8")
    (inbox / "uncertain_idea.md.review").write_text(REVIEW_MD, encoding="utf-8")
    (inbox / "shipped_note.md.done").write_text(DONE_MD, encoding="utf-8")
    (inbox / "parked_note.md.archive").write_text(ARCHIVE_MD, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


async def _post(client: TestClient, path: str, body: dict):
    res = await client.post(path, json=body)
    return res.status, await res.json()


# ── List ─────────────────────────────────────────────────────────────────────


async def test_list_states_and_counts(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/inbox/review")
        assert res.status == 200
        body = await res.json()

    assert body["ok"] is True
    data = body["data"]
    assert data["counts"] == {"pending": 1, "review": 1, "approved": 1, "rejected": 1}

    by_name = {r["filename"]: r for r in data["items"]}
    assert by_name["wire_routes.md"]["state"] == "pending"
    assert by_name["wire_routes.md"]["title"] == "Wire the deck routes"
    assert by_name["wire_routes.md"]["preview"].startswith("Add the review queue task")
    assert by_name["uncertain_idea.md.review"]["state"] == "review"
    assert by_name["uncertain_idea.md.review"]["reason"] == "Potential duplicate of 'older idea'"
    assert by_name["shipped_note.md.done"]["state"] == "approved"
    assert by_name["parked_note.md.archive"]["state"] == "rejected"
    for row in data["items"]:
        assert row["mtime"] > 0 and row["size"] > 0


async def test_list_state_filter_and_bad_state(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/inbox/review?state=pending")
        assert res.status == 200
        rows = (await res.json())["data"]["items"]
        assert [r["filename"] for r in rows] == ["wire_routes.md"]

        res = await client.get("/api/deck/inbox/review?state=bogus")
        assert res.status == 400


async def test_list_empty_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    bare = tmp_path / "bare"
    (bare / ".navig").mkdir(parents=True)  # marker only — no inbox dir
    monkeypatch.chdir(bare)

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/inbox/review")
        assert res.status == 200
        data = (await res.json())["data"]
    assert data["items"] == []
    assert data["counts"] == {"pending": 0, "review": 0, "approved": 0, "rejected": 0}


async def test_list_unknown_space_is_404(space, monkeypatch):
    monkeypatch.setattr(review_routes, "_space_root", lambda s: None)
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/inbox/review?space=ghost")
        assert res.status == 404
        body = await res.json()
    assert body["ok"] is False and "ghost" in body["error"]


async def test_list_resolves_space_id_to_its_root(space, tmp_path, monkeypatch):
    # cwd sits in a DIFFERENT project; ?space= must still hit the fixture space.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".navig").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(
        review_routes, "_space_root", lambda s: space if s == "myspace" else None
    )

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/inbox/review?space=myspace")
        assert res.status == 200
        body = await res.json()
    assert body["data"]["counts"]["pending"] == 1


# ── Item detail ──────────────────────────────────────────────────────────────


async def test_item_detail_and_missing(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get(
            "/api/deck/inbox/review/item", params={"name": "uncertain_idea.md.review"}
        )
        assert res.status == 200
        data = (await res.json())["data"]
        assert data["state"] == "review"
        assert data["content"] == REVIEW_MD
        assert data["frontmatter"]["title"] == "Uncertain idea"
        assert "human decision" in data["body"]

        res = await client.get("/api/deck/inbox/review/item", params={"name": "missing.md"})
        assert res.status == 404


async def test_item_rejects_traversal_names(space):
    secret = space / "secrets.md"
    secret.write_text("nope", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        for bad in ("../secrets.md", "..\\secrets.md", "/etc/passwd", ".hidden.md", ""):
            res = await client.get("/api/deck/inbox/review/item", params={"name": bad})
            assert res.status == 400, bad
    assert secret.read_text(encoding="utf-8") == "nope"


# ── Sandbox (analyse) ────────────────────────────────────────────────────────


async def test_analyse_routes_by_keyword_without_side_effects(space):
    inbox = space / ".navig" / "inbox"
    before = sorted(p.name for p in inbox.iterdir())

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/inbox/review/analyse", {"name": "wire_routes.md"}
        )
        assert status == 200
        data = body["data"]

    assert data["decision"] == "route"
    assert data["target_dir"] == "plans/tasks/active"  # 'task' keyword wins
    assert data["named_targets"]["wiki"] == "wiki/knowledge"
    assert data["named_targets"]["plans"] == "plans/tasks/active"
    assert data["title"] == "Wire the deck routes"

    # NO side effects: no file moved, no staging queue appended.
    assert sorted(p.name for p in inbox.iterdir()) == before
    assert not (space / ".navig" / "staging" / "reconciliation_queue.json").exists()


async def test_analyse_decision_route_targets(space):
    inbox = space / ".navig" / "inbox"
    (inbox / "storage_choice.md").write_text(DECISION_MD, encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/inbox/review/analyse", {"name": "storage_choice.md"}
        )
        assert status == 200
        assert body["data"]["target_dir"] == "plans/decisions"

        status, body = await _post(client, "/api/deck/inbox/review/analyse", {"name": "nope.md"})
        assert status == 404
        status, _ = await _post(client, "/api/deck/inbox/review/analyse", {})
        assert status == 400


async def test_analyse_flags_duplicates(space):
    inbox = space / ".navig" / "inbox"
    (inbox / "wire_routes_again.md").write_text(
        "---\ntitle: Wire the deck routes\n---\n\nSame idea twice.\n", encoding="utf-8"
    )

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/inbox/review/analyse", {"name": "wire_routes_again.md"}
        )
        assert status == 200
        data = body["data"]
    assert data["decision"] == "duplicate"
    assert data["duplicate_of"] == "wire_routes.md"


# ── Approve ──────────────────────────────────────────────────────────────────


async def test_approve_default_target_routes_and_marks_done(space):
    inbox = space / ".navig" / "inbox"

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/inbox/review/approve", {"name": "wire_routes.md"}
        )
        assert status == 200
        data = body["data"]

    assert data["state"] == "approved"
    assert data["routed_to"] == "plans/tasks/active/wire_routes.md"
    routed = space / ".navig" / "plans" / "tasks" / "active" / "wire_routes.md"
    assert routed.read_text(encoding="utf-8") == TASK_MD  # byte-preserved copy
    assert not (inbox / "wire_routes.md").exists()
    assert (inbox / "wire_routes.md.done").is_file()  # history survives

    # Audit line lands in the shared staging queue and feeds the list's routed_to.
    queue = space / ".navig" / "staging" / "reconciliation_queue.json"
    lines = [json.loads(x) for x in queue.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines[-1]["item"] == "wire_routes.md"
    assert lines[-1]["decision"] == "approved"
    assert lines[-1]["target"] == "plans/tasks/active/wire_routes.md"

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/inbox/review?state=approved")
        rows = (await res.json())["data"]["items"]
    by_name = {r["name"]: r for r in rows}
    assert by_name["wire_routes.md"]["routed_to"] == "plans/tasks/active/wire_routes.md"


async def test_approve_named_targets_and_review_state(space):
    async with TestClient(TestServer(_app())) as client:
        # Approving a .md.review item with a named target routes it to wiki.
        status, body = await _post(
            client,
            "/api/deck/inbox/review/approve",
            {"name": "uncertain_idea.md.review", "target": "wiki"},
        )
        assert status == 200
        data = body["data"]

    assert data["target_dir"] == "wiki/knowledge"
    assert (space / ".navig" / "wiki" / "knowledge" / "uncertain_idea.md").is_file()
    inbox = space / ".navig" / "inbox"
    assert (inbox / "uncertain_idea.md.done").is_file()
    assert not (inbox / "uncertain_idea.md.review").exists()


async def test_approve_rejects_escaping_targets(space):
    inbox = space / ".navig" / "inbox"

    async with TestClient(TestServer(_app())) as client:
        for bad in ("../../outside", "..", "/abs/path", "C:\\evil", ""):
            status, _ = await _post(
                client,
                "/api/deck/inbox/review/approve",
                {"name": "wire_routes.md", "target": bad},
            )
            # "" falls back to the router default → 200; everything else is 400.
            assert status == (200 if bad == "" else 400), bad
            if bad != "":
                assert (inbox / "wire_routes.md").is_file() or (
                    inbox / "wire_routes.md.done"
                ).is_file()

        status, _ = await _post(
            client, "/api/deck/inbox/review/approve", {"name": "wire_routes.md", "target": 42}
        )
        assert status == 400
    assert not (space / "outside").exists()


async def test_approve_collision_gets_unique_name(space):
    target = space / ".navig" / "plans" / "tasks" / "active"
    target.mkdir(parents=True)
    (target / "wire_routes.md").write_text("older twin", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/inbox/review/approve", {"name": "wire_routes.md"}
        )
        assert status == 200
        assert body["data"]["routed_to"] == "plans/tasks/active/wire_routes_1.md"

    assert (target / "wire_routes.md").read_text(encoding="utf-8") == "older twin"
    assert (target / "wire_routes_1.md").read_text(encoding="utf-8") == TASK_MD


# ── Reject / requeue ─────────────────────────────────────────────────────────


async def test_reject_parks_and_requeue_restores(space):
    inbox = space / ".navig" / "inbox"

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/inbox/review/reject", {"name": "wire_routes.md"}
        )
        assert status == 200
        assert body["data"]["state"] == "rejected"
        assert (inbox / "wire_routes.md.archive").is_file()
        assert not (inbox / "wire_routes.md").exists()

        # Undo — back to the pending queue, content intact.
        status, body = await _post(
            client, "/api/deck/inbox/review/requeue", {"name": "wire_routes.md.archive"}
        )
        assert status == 200
        assert body["data"]["state"] == "pending"
        assert (inbox / "wire_routes.md").read_text(encoding="utf-8") == TASK_MD

        # Requeue also un-parks approved items.
        status, body = await _post(
            client, "/api/deck/inbox/review/requeue", {"name": "shipped_note.md.done"}
        )
        assert status == 200
        assert (inbox / "shipped_note.md").is_file()

        status, _ = await _post(client, "/api/deck/inbox/review/reject", {"name": "ghost.md"})
        assert status == 404
        status, _ = await _post(
            client, "/api/deck/inbox/review/requeue", {"name": "../escape.md"}
        )
        assert status == 400
