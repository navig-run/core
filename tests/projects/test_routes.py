"""The /api/deck/projects routes over real HTTP, against an isolated registry."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
pytest.importorskip("aiohttp")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from navig.gateway.deck.routes import projects as routes  # noqa: E402


def _app() -> web.Application:
    app = web.Application()
    routes.register(app)
    return app


async def _json(res):
    return res.status, await res.json()


@pytest.fixture()
def registered(project: Path) -> str:
    from navig.projects.model import add_project

    return add_project(project)["id"]


async def test_add_scan_and_list(isolated: Path, project: Path) -> None:
    bare = isolated / "apps" / "tool"
    bare.mkdir()
    (bare / "pyproject.toml").write_text('[project]\nname = "tool"\n', encoding="utf-8")
    (isolated / "apps" / "photos").mkdir()  # neither .navig nor code — ignored

    async with TestClient(TestServer(_app())) as client:
        status, body = await _json(await client.post("/api/deck/projects/scan-root",
                                                     json={"path": str(isolated / "apps"), "add": True}))
        assert status == 200, body
        cands = {c["name"]: c for c in body["data"]["candidates"]}
        assert set(cands) == {"shop", "tool"}
        assert cands["shop"]["registered"] is True and cands["shop"]["id"] == "shop"
        assert cands["tool"]["registered"] is False  # suggested only — no .navig/ written
        assert not (bare / ".navig").exists()

        status, body = await _json(await client.post("/api/deck/projects/add", json={"path": str(bare)}))
        assert status == 200 and body["data"]["created"] == [".navig/"]

        status, body = await _json(await client.get("/api/deck/projects"))
        assert status == 200
        by_id = {p["id"]: p for p in body["data"]}
        assert {"shop", "tool"} <= set(by_id)
        shop = by_id["shop"]
        assert shop["kind"] == "code" and shop["scripts"] == 7
        assert shop["phase"]["title"] == "Payments"
        assert shop["todos"]["open"] >= 3


async def test_add_rejects_a_missing_folder(isolated: Path) -> None:
    async with TestClient(TestServer(_app())) as client:
        status, body = await _json(await client.post("/api/deck/projects/add",
                                                     json={"path": str(isolated / "nope")}))
        assert status == 400 and "not a folder" in body["error"]


async def test_detail_docs_and_unknown_project(registered: str) -> None:
    async with TestClient(TestServer(_app())) as client:
        status, body = await _json(await client.get(f"/api/deck/projects/{registered}"))
        assert status == 200
        d = body["data"]
        assert d["doc_health"]["missing"] == []
        assert any(s["key"] == "npm:apps/web:dev" for s in d["profile"]["scripts"])
        assert d["next_action"]["text"] == "Refactor payments"  # in-progress beats open

        status, _ = await _json(await client.get("/api/deck/projects/nope"))
        assert status == 404


@pytest.mark.parametrize("rel", ["../secret.md", ".navig/vault/secret.md", "secret.md"])
async def test_doc_routes_refuse_anything_unlisted(registered: str, rel: str) -> None:
    async with TestClient(TestServer(_app())) as client:
        status, _ = await _json(await client.get(f"/api/deck/projects/{registered}/doc", params={"path": rel}))
        assert status == 400
        status, _ = await _json(await client.post(f"/api/deck/projects/{registered}/doc",
                                                  json={"path": rel, "content": "x"}))
        assert status == 400


async def test_doc_save_conflict_is_409(registered: str, project: Path) -> None:
    rel = ".navig/plans/VISION.md"
    async with TestClient(TestServer(_app())) as client:
        _, body = await _json(await client.get(f"/api/deck/projects/{registered}/doc", params={"path": rel}))
        fp = body["data"]["fingerprint"]
        (project / rel).write_bytes(b"# edited elsewhere\n")
        status, body = await _json(await client.post(f"/api/deck/projects/{registered}/doc",
                                                     json={"path": rel, "content": "# mine\n", "expect": fp}))
        assert status == 409
    assert (project / rel).read_bytes() == b"# edited elsewhere\n"


async def test_todo_state_and_promote(registered: str, project: Path) -> None:
    async with TestClient(TestServer(_app())) as client:
        _, body = await _json(await client.get(f"/api/deck/projects/{registered}/todos"))
        t = next(x for x in body["data"]["todos"] if x["text"] == "Wire the checkout")
        status, body = await _json(await client.post(
            f"/api/deck/projects/{registered}/todos/state",
            json={"path": t["path"], "line": t["line"], "state": "done", "expect": t["fingerprint"]}))
        assert status == 200 and body["data"]["todo"]["state"] == "done"

        status, body = await _json(await client.post(
            f"/api/deck/projects/{registered}/todos/promote",
            json={"path": t["path"], "line": t["line"]}))
        assert status == 200 and body["data"]["created"] is True
        status, body = await _json(await client.post(
            f"/api/deck/projects/{registered}/todos/promote",
            json={"path": t["path"], "line": t["line"]}))
        assert status == 200 and body["data"]["created"] is False  # deduped by origin_ref

    from navig.store.board import get_board_store

    store = get_board_store()
    assert Path(store.db_path).is_relative_to(Path(project).parents[1])  # isolated, not live


async def test_context_and_prefs(registered: str) -> None:
    async with TestClient(TestServer(_app())) as client:
        status, body = await _json(await client.get(f"/api/deck/projects/{registered}/context"))
        assert status == 200 and body["data"]["report"]["total_injected"] > 0
        status, body = await _json(await client.patch(f"/api/deck/projects/{registered}/prefs",
                                                      json={"pinned": True, "bogus": 1}))
        assert status == 200 and body["data"] == {"pinned": True}
        _, body = await _json(await client.get("/api/deck/projects"))
        assert body["data"][0]["id"] == registered and body["data"][0]["prefs"]["pinned"] is True


async def test_the_list_answers_without_waiting_for_git_and_git_full_waits(
    registered: str, project: Path
) -> None:
    """HTTP layer for the fast default and its `?git=full` escape hatch.

    Regression for a cold list that took 10-14 s while NAVIG OS gives a brain call
    15 s: `git_state()` used to run inline for every repo before the list could answer,
    so a change that puts it back would slow every call and nothing here would notice.
    The default must answer with `git.pending` and the branch (read from `.git/HEAD`);
    asking for `git=full` must wait for the real numbers instead.
    """
    import subprocess

    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)

    async with TestClient(TestServer(_app())) as client:
        status, body = await _json(await client.get("/api/deck/projects?refresh=1"))
        assert status == 200, body
        fast = {p["id"]: p for p in body["data"]}[registered]["git"]
        assert fast.get("pending") is True, "the default must not wait on git"
        assert fast.get("branch"), "the branch comes from .git/HEAD with no git process"
        assert fast.get("dirty") is None

        status, body = await _json(await client.get("/api/deck/projects?refresh=1&git=full"))
        assert status == 200, body
        exact = {p["id"]: p for p in body["data"]}[registered]["git"]
        assert exact.get("pending") is not True, "git=full waits, so nothing is left unread"
        assert exact.get("dirty") is not None
