"""Contract tests for the /api/deck/plans routes (the project command center).

Exercises the plans engine over real HTTP (TestClient) against a temp space
fixture: state snapshot, doc read/write + traversal confinement, checkbox
toggling, phase lifecycle, git milestone commit/rollback (confirm-gated,
dirty-tree-protected), and scaffold generation (skip-existing).

All filesystem state is isolated: NAVIG_DATA_DIR → tmp_path, the fixture
space carries its own ``.navig/plans`` tree, and git tests init a private
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

import navig.gateway.deck.routes.plans as plans_routes

_HAS_GIT = shutil.which("git") is not None

PHASE_MD = """---
phase: 07
title: Plan Engine
started: 2026-07-01
milestone: MVP1
status: active
blocked_by: ~
---

## Objective

Ship the plans engine over deck routes.

## Active Tasks

- Wire the deck routes
- Render the command center
"""

NEXT_PHASE_MD = """---
phase: 08
title: OS Surface
started: 2026-07-11
milestone: MVP1
status: active
blocked_by: ~
---

## Objective

Render the command center in the OS app.
"""

MILESTONE_MD = """---
title: First Milestone
status: active
target_date: 2026-08-01
---

# MVP1

## Tasks

- [x] Parse plans
- [ ] Expose routes
- [ ] Render UI
"""

DEV_PLAN_MD = """# DEV Plan

## Added Goals

- [ ] First goal
- [x] Second goal
not a checkbox line
"""


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/plans/state", plans_routes.handle_plans_state)
    app.router.add_get("/api/deck/plans/doc", plans_routes.handle_plans_doc_get)
    app.router.add_post("/api/deck/plans/doc", plans_routes.handle_plans_doc_save)
    app.router.add_post("/api/deck/plans/toggle-task", plans_routes.handle_plans_toggle_task)
    app.router.add_post("/api/deck/plans/phase/advance", plans_routes.handle_plans_phase_advance)
    app.router.add_post("/api/deck/plans/phase/block", plans_routes.handle_plans_phase_block)
    app.router.add_post("/api/deck/plans/phase/unblock", plans_routes.handle_plans_phase_unblock)
    app.router.add_post("/api/deck/plans/phase/complete", plans_routes.handle_plans_phase_complete)
    app.router.add_post(
        "/api/deck/plans/milestone/commit", plans_routes.handle_plans_milestone_commit
    )
    app.router.add_post(
        "/api/deck/plans/milestone/rollback", plans_routes.handle_plans_milestone_rollback
    )
    app.router.add_post("/api/deck/plans/generate", plans_routes.handle_plans_generate)
    return app


@pytest.fixture()
def space(tmp_path, monkeypatch):
    """A temp space with a full .navig/plans tree; cwd bound to its root.

    The .navig/ marker stops the project-root walk-up at the fixture — without
    it the walk would escape tmp and resolve to the real repo's .navig.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "space"
    plans = root / ".navig" / "plans"
    (plans / "milestones").mkdir(parents=True)
    (plans / "phases").mkdir()
    (plans / "CURRENT_PHASE.md").write_text(PHASE_MD, encoding="utf-8")
    (plans / "DEV_PLAN.md").write_text(DEV_PLAN_MD, encoding="utf-8")
    (plans / "milestones" / "MVP1.md").write_text(MILESTONE_MD, encoding="utf-8")
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
    assert _git(root, "commit", "-q", "-m", "init").returncode == 0


async def _post(client: TestClient, path: str, body: dict):
    res = await client.post(path, json=body)
    return res.status, await res.json()


# ── State ────────────────────────────────────────────────────────────────────


async def test_state_happy_path(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/plans/state")
        assert res.status == 200
        body = await res.json()

    assert body["ok"] is True
    data = body["data"]

    phase = data["phase"]
    assert phase["phase"] == "07"
    assert phase["title"] == "Plan Engine"
    assert phase["milestone"] == "MVP1"
    assert phase["status"] == "active"
    assert phase["blocked_by"] == "~"
    assert phase["active_tasks"] == ["Wire the deck routes", "Render the command center"]

    progress = data["progress"]
    assert progress["done"] == 1 and progress["total"] == 3
    ms = progress["milestones"][0]
    assert ms["name"] == "MVP1" and ms["title"] == "First Milestone"

    docs = {d["path"]: d for d in data["docs"]}
    assert "CURRENT_PHASE.md" in docs
    assert docs["milestones/MVP1.md"]["title"] == "MVP1"  # first H1 wins
    assert docs["DEV_PLAN.md"]["title"] == "DEV Plan"
    assert docs["DEV_PLAN.md"]["size"] > 0 and docs["DEV_PLAN.md"]["mtime"] > 0


async def test_state_without_plans_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    bare = tmp_path / "bare"
    (bare / ".navig").mkdir(parents=True)  # marker only — no plans dir
    monkeypatch.chdir(bare)

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/plans/state")
        assert res.status == 200
        body = await res.json()

    data = body["data"]
    assert data["phase"] is None
    assert data["docs"] == []
    assert data["progress"]["total"] == 0


async def test_state_unknown_space_is_404(space, monkeypatch):
    monkeypatch.setattr(plans_routes, "_space_root", lambda s: None)
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/plans/state?space=ghost")
        assert res.status == 404
        body = await res.json()
    assert body["ok"] is False and "ghost" in body["error"]


async def test_state_resolves_space_id_to_its_root(space, tmp_path, monkeypatch):
    # cwd sits in a DIFFERENT project; ?space= must still hit the fixture space.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".navig").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(
        plans_routes, "_space_root", lambda s: space if s == "myspace" else None
    )

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/plans/state?space=myspace")
        assert res.status == 200
        body = await res.json()
    assert body["data"]["phase"]["phase"] == "07"


# ── Docs ─────────────────────────────────────────────────────────────────────


async def test_doc_read_write_roundtrip(space):
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/plans/doc?path=DEV_PLAN.md")
        assert res.status == 200
        data = (await res.json())["data"]
        assert data["path"] == "DEV_PLAN.md"
        assert data["content"] == DEV_PLAN_MD
        assert data["mtime"] > 0

        # Create a NEW doc in a subfolder, then read it back.
        status, body = await _post(
            client, "/api/deck/plans/doc",
            {"path": "notes/NEW.md", "content": "# New Note\n\n- [ ] follow up\n"},
        )
        assert status == 200 and body["ok"] is True
        assert body["data"]["path"] == "notes/NEW.md"

        res = await client.get("/api/deck/plans/doc?path=notes/NEW.md")
        assert res.status == 200
        assert (await res.json())["data"]["content"] == "# New Note\n\n- [ ] follow up\n"

    assert (space / ".navig" / "plans" / "notes" / "NEW.md").is_file()


async def test_doc_traversal_and_bad_paths_rejected(space):
    secret = space / "secrets.md"
    secret.write_text("nope", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        for bad in ("../../secrets.md", "..\\..\\secrets.md", "/etc/passwd", ""):
            res = await client.get("/api/deck/plans/doc", params={"path": bad})
            assert res.status == 400, bad

        status, _ = await _post(
            client, "/api/deck/plans/doc",
            {"path": "../../escape.md", "content": "x"},
        )
        assert status == 400
        status, body = await _post(
            client, "/api/deck/plans/doc", {"path": "evil.txt", "content": "x"}
        )
        assert status == 400 and ".md" in body["error"]

        res = await client.get("/api/deck/plans/doc?path=missing.md")
        assert res.status == 404

    assert not (space / "escape.md").exists()
    assert secret.read_text(encoding="utf-8") == "nope"


# ── Toggle task ──────────────────────────────────────────────────────────────


async def test_toggle_task_flips_checkbox_both_ways(space):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/plans/toggle-task", {"path": "DEV_PLAN.md", "line": 4}
        )
        assert status == 200 and body["data"]["checked"] is True
        on_disk = (space / ".navig" / "plans" / "DEV_PLAN.md").read_text(encoding="utf-8")
        assert "- [x] First goal" in on_disk
        assert body["data"]["content"] == on_disk

        status, body = await _post(
            client, "/api/deck/plans/toggle-task", {"path": "DEV_PLAN.md", "line": 4}
        )
        assert status == 200 and body["data"]["checked"] is False
        assert "- [ ] First goal" in (space / ".navig" / "plans" / "DEV_PLAN.md").read_text(
            encoding="utf-8"
        )


async def test_toggle_task_rejects_non_checkbox_and_bad_lines(space):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/plans/toggle-task", {"path": "DEV_PLAN.md", "line": 6}
        )
        assert status == 400 and "not a markdown checkbox" in body["error"]

        status, _ = await _post(
            client, "/api/deck/plans/toggle-task", {"path": "DEV_PLAN.md", "line": 999}
        )
        assert status == 400
        status, _ = await _post(
            client, "/api/deck/plans/toggle-task", {"path": "DEV_PLAN.md", "line": -1}
        )
        assert status == 400
        status, _ = await _post(
            client, "/api/deck/plans/toggle-task", {"path": "../DEV_PLAN.md", "line": 0}
        )
        assert status == 400


# ── Phase lifecycle ──────────────────────────────────────────────────────────


async def test_phase_block_unblock_complete(space):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/plans/phase/block", {})
        assert status == 400  # reason required

        status, body = await _post(
            client, "/api/deck/plans/phase/block", {"reason": "waiting on review"}
        )
        assert status == 200
        assert body["data"]["phase"]["status"] == "blocked"
        assert body["data"]["phase"]["blocked_by"] == "waiting on review"

        status, body = await _post(client, "/api/deck/plans/phase/unblock", {})
        assert status == 200
        assert body["data"]["phase"]["status"] == "active"
        assert body["data"]["phase"]["blocked_by"] == "~"

        status, body = await _post(client, "/api/deck/plans/phase/complete", {})
        assert status == 200
        assert body["data"]["phase"]["status"] == "completed"


async def test_phase_advance_installs_next_file(space):
    plans = space / ".navig" / "plans"
    (plans / "phases" / "phase_08.md").write_text(NEXT_PHASE_MD, encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(client, "/api/deck/plans/phase/advance", {})
        assert status == 400  # next_phase_file required

        status, _ = await _post(
            client, "/api/deck/plans/phase/advance", {"next_phase_file": "../evil.md"}
        )
        assert status == 400
        status, _ = await _post(
            client, "/api/deck/plans/phase/advance", {"next_phase_file": "phases/nope.md"}
        )
        assert status == 404

        status, body = await _post(
            client, "/api/deck/plans/phase/advance", {"next_phase_file": "phases/phase_08.md"}
        )
        assert status == 200
        assert body["data"]["phase"]["phase"] == "08"
        assert body["data"]["phase"]["title"] == "OS Surface"


# ── Milestone commit / rollback (git) ────────────────────────────────────────


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
async def test_milestone_commit_creates_scoped_commit(space):
    _init_repo(space)
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/plans/milestone/commit", {})
        assert status == 400  # name required

        status, body = await _post(
            client, "/api/deck/plans/milestone/commit", {"name": "MVP1 shipped"}
        )
        assert status == 200
        data = body["data"]
        assert len(data["sha"]) == 40
        assert data["message"] == "[navig] Milestone: MVP1 shipped"

    subject = _git(space, "log", "-1", "--pretty=%s").stdout.strip()
    assert subject == "[navig] Milestone: MVP1 shipped"
    changelog = (space / ".navig" / "plans" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Milestone: MVP1 shipped" in changelog


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
async def test_milestone_commit_rejects_non_repo_and_ignored_plans(space, tmp_path, monkeypatch):
    async with TestClient(TestServer(_app())) as client:
        # Fixture space is not (yet) a git repo → 400.
        status, body = await _post(
            client, "/api/deck/plans/milestone/commit", {"name": "M1"}
        )
        assert status == 400 and "not a git repository" in body["error"]

    # A repo whose .gitignore hides .navig/ entirely → "nothing to commit".
    (space / ".gitignore").write_text(".navig/\n", encoding="utf-8")
    _init_repo(space)
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/plans/milestone/commit", {"name": "M1"}
        )
        assert status == 400 and body["error"] == "nothing to commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
async def test_milestone_rollback_confirm_gate_and_reset(space):
    _init_repo(space)
    plans = space / ".navig" / "plans"

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/plans/milestone/commit", {"name": "checkpoint"}
        )
        assert status == 200
        milestone_sha = body["data"]["sha"]

        # Drift past the checkpoint with a plain commit inside .navig/plans.
        (plans / "DEV_PLAN.md").write_text(DEV_PLAN_MD + "\n- [ ] Third goal\n", encoding="utf-8")
        assert _git(space, "add", "-A").returncode == 0
        assert _git(space, "commit", "-q", "-m", "wip").returncode == 0

        # Confirm gate: anything but the literal "ROLLBACK" is refused.
        status, _ = await _post(client, "/api/deck/plans/milestone/rollback", {})
        assert status == 400
        status, _ = await _post(
            client, "/api/deck/plans/milestone/rollback", {"confirm": "yes"}
        )
        assert status == 400

        status, body = await _post(
            client, "/api/deck/plans/milestone/rollback", {"confirm": "ROLLBACK"}
        )
        assert status == 200
        assert body["data"]["resetTo"] == milestone_sha
        assert body["data"]["subject"] == "[navig] Milestone: checkpoint"

    assert _git(space, "rev-parse", "HEAD").stdout.strip() == milestone_sha
    assert "Third goal" not in (plans / "DEV_PLAN.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
async def test_milestone_rollback_refuses_dirty_tree_outside_plans(space):
    _init_repo(space)
    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(
            client, "/api/deck/plans/milestone/commit", {"name": "checkpoint"}
        )
        assert status == 200

        # Uncommitted USER work outside .navig/plans must block the hard reset.
        (space / "app.txt").write_text("precious uncommitted work", encoding="utf-8")
        status, body = await _post(
            client, "/api/deck/plans/milestone/rollback", {"confirm": "ROLLBACK"}
        )
        assert status == 400 and "outside .navig/plans" in body["error"]

    assert (space / "app.txt").read_text(encoding="utf-8") == "precious uncommitted work"


# ── Generate ─────────────────────────────────────────────────────────────────


async def test_generate_scaffolds_then_skips_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    fresh = tmp_path / "fresh"
    (fresh / ".navig").mkdir(parents=True)  # walk-up marker
    monkeypatch.chdir(fresh)

    custom_vision = "---\ntitle: My Vision\n---\n\n# Vision\n\nHand-written.\n"

    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(client, "/api/deck/plans/generate", {"mode": "llm"})
        assert status == 400  # only scaffold/ai modes exist

        status, body = await _post(client, "/api/deck/plans/generate", {"mode": "scaffold"})
        assert status == 200
        created = body["data"]["created"]
        assert "plans/VISION.md" in created
        assert "plans/DEV_PLAN.md" in created
        assert "plans/phases/CURRENT_PHASE.md" in created
        assert body["data"]["skipped"] == []

        # Existing (edited) docs are never overwritten on a re-run.
        (fresh / ".navig" / "plans" / "VISION.md").write_text(custom_vision, encoding="utf-8")
        status, body = await _post(client, "/api/deck/plans/generate", {})
        assert status == 200
        assert "plans/VISION.md" in body["data"]["skipped"]
        assert body["data"]["created"] == []

    assert (fresh / ".navig" / "plans" / "VISION.md").read_text(encoding="utf-8") == custom_vision


# ── Generate (mode=ai — agent-layer drafting, LLM monkeypatched) ─────────────

VISION_MD = "---\ntitle: Vision\n---\n\n# Vision\n\nShip a tiny CLI that syncs notes.\n"

# The drafted suite = every scaffold template EXCEPT the vision seed itself.
AI_SUITE = [
    "plans/ROADMAP.md",
    "plans/SPEC.md",
    "plans/DEV_PLAN.md",
    "plans/phases/CURRENT_PHASE.md",
    "plans/milestones/MVP1_initial_milestone.md",
]


@pytest.fixture()
def ai_space(tmp_path, monkeypatch):
    """A temp space carrying only a hand-written VISION.md."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "aispace"
    plans = root / ".navig" / "plans"
    plans.mkdir(parents=True)
    (plans / "VISION.md").write_text(VISION_MD, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _patch_drafter(monkeypatch, fn):
    """Patch the agent seam the route lazily imports per call."""
    import navig.agent.plan_drafter as plan_drafter

    monkeypatch.setattr(plan_drafter, "draft_plan_doc", fn)


async def test_generate_ai_drafts_missing_and_skips_existing(ai_space, monkeypatch):
    calls: list[dict] = []

    def fake_draft(**kwargs):
        calls.append(kwargs)
        return f"# {kwargs['doc_name']}\n\n- [ ] drafted from vision"

    _patch_drafter(monkeypatch, fake_draft)
    plans = ai_space / ".navig" / "plans"
    (plans / "ROADMAP.md").write_text("# My Roadmap\n\nHand-written.\n", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/plans/generate", {"mode": "ai"})
        assert status == 200

    data = body["data"]
    assert data["mode"] == "ai"
    assert data["skipped"] == ["plans/ROADMAP.md"]
    assert data["errors"] == []
    assert sorted(data["created"]) == sorted(p for p in AI_SUITE if p != "plans/ROADMAP.md")

    # VISION.md is the seed — never drafted, never touched.
    assert (plans / "VISION.md").read_text(encoding="utf-8") == VISION_MD
    assert (plans / "ROADMAP.md").read_text(encoding="utf-8") == "# My Roadmap\n\nHand-written.\n"
    assert "drafted from vision" in (plans / "DEV_PLAN.md").read_text(encoding="utf-8")
    assert (plans / "phases" / "CURRENT_PHASE.md").is_file()

    # Every draft was seeded with the actual vision text + its template skeleton.
    assert calls and all("syncs notes" in c["vision"] for c in calls)
    assert all(c["template"].strip() for c in calls)
    assert not any(c["doc_name"] == "VISION.md" for c in calls)


async def test_generate_ai_requires_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    bare = tmp_path / "bare"
    (bare / ".navig").mkdir(parents=True)
    monkeypatch.chdir(bare)
    _patch_drafter(monkeypatch, lambda **kw: "# draft")

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/plans/generate", {"mode": "ai"})
        assert status == 400
        assert "VISION.md" in body["error"]

        # An inline vision string is an accepted alternative seed…
        status, body = await _post(
            client, "/api/deck/plans/generate", {"mode": "ai", "vision": "Build a bird feeder cam."}
        )
        assert status == 200
        assert sorted(body["data"]["created"]) == sorted(AI_SUITE)

    # …and it must NOT materialize a VISION.md on disk.
    assert not (bare / ".navig" / "plans" / "VISION.md").exists()


async def test_generate_ai_maps_no_llm_to_503(ai_space, monkeypatch):
    from navig.agent.plan_drafter import PlanDraftUnavailableError

    def no_llm(**kwargs):
        raise PlanDraftUnavailableError("No AI backend is configured or reachable — connect a provider.")

    _patch_drafter(monkeypatch, no_llm)

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/plans/generate", {"mode": "ai"})
        assert status == 503
        assert "No AI backend" in body["error"]

    plans = ai_space / ".navig" / "plans"
    for rel in AI_SUITE:
        assert not (ai_space / ".navig" / rel).exists(), rel
    assert (plans / "VISION.md").read_text(encoding="utf-8") == VISION_MD


async def test_generate_ai_one_doc_failure_is_partial_success(ai_space, monkeypatch):
    def flaky_draft(**kwargs):
        if kwargs["doc_name"] == "SPEC.md":
            raise ValueError("draft blew up")
        return f"# {kwargs['doc_name']}\n\nok"

    _patch_drafter(monkeypatch, flaky_draft)

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/plans/generate", {"mode": "ai"})
        assert status == 200

    data = body["data"]
    assert data["errors"] == [{"doc": "plans/SPEC.md", "error": "draft blew up"}]
    assert sorted(data["created"]) == sorted(p for p in AI_SUITE if p != "plans/SPEC.md")
    assert not (ai_space / ".navig" / "plans" / "SPEC.md").exists()


async def test_generate_ai_force_redrafts_but_never_vision(ai_space, monkeypatch):
    _patch_drafter(monkeypatch, lambda **kw: f"# {kw['doc_name']}\n\nredrafted")
    plans = ai_space / ".navig" / "plans"
    (plans / "ROADMAP.md").write_text("# Old Roadmap\n", encoding="utf-8")

    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/plans/generate", {"mode": "ai", "force": True}
        )
        assert status == 200

    assert "plans/ROADMAP.md" in body["data"]["created"]
    assert "redrafted" in (plans / "ROADMAP.md").read_text(encoding="utf-8")
    assert (plans / "VISION.md").read_text(encoding="utf-8") == VISION_MD  # force ≠ touch the seed
