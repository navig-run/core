"""/api/deck/recall — session links over real loopback HTTP.

The routes expose titles and paths derived from the operator's own transcripts, so they must
stay local: a browser extension (trusted elsewhere on loopback) and anything that is not a
genuinely-local request get 403; a machine where Recall was never enabled gets 409 with the
command that enables it. All state lives in tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

import navig.gateway.deck.routes.recall as recall_routes
from navig.recall.store import RecallStore
from navig.recall.vault import Vault

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def bank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RecallStore:
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    store = RecallStore(tmp_path / "index.db")
    store.upsert_source({"source_id": f"cc:{SID}", "adapter": "claude_code", "path": "x.jsonl", "project": "alpha",
                         "title": "Wire the store", "cwd": r"E:\proj\alpha", "last_ts": "2026-10-01T10:00:00Z",
                         "meta": {"git_branch": "feat/store"}})
    store.add_links([(f"cc:{SID}", "file:alpha/core/store.py", "touched"), (f"cc:{SID}", "commit:a1b2c3d", "committed")])
    monkeypatch.setattr("navig.recall.store.get_recall_store", lambda: store)
    monkeypatch.setattr("navig.recall.vault.Vault", lambda: Vault(tmp_path / "vault"))
    monkeypatch.setattr(recall_routes, "_enabled", lambda: True)
    from navig.recall import projects

    monkeypatch.setattr(projects, "_registry_entries", lambda: [("alpha", r"E:\proj\alpha")])
    return store


async def _call(method: str, path: str, *, headers: dict[str, str] | None = None,
                body: dict | None = None) -> tuple[int, dict]:
    app = web.Application()
    recall_routes.register(app)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"http://127.0.0.1:{server.port}{path}", headers=headers or {},
                                       data=json.dumps(body) if body is not None else None,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status, await resp.json()
    finally:
        await server.close()


async def test_sessions_for_a_file_and_a_commit(bank) -> None:
    status, out = await _call("GET", "/api/deck/recall/sessions?path=E:%5Cproj%5Calpha%5Ccore%5Cstore.py")
    assert status == 200, out
    (row,) = out["data"]["sessions"]
    assert (row["session_id"], row["branch"], row["rels"]) == (SID, "feat/store", ["touched"])
    assert row["open_uri"] == f"vscode://anthropic.claude-code/open?session={SID}"
    status, out = await _call("GET", "/api/deck/recall/sessions?commit=A1B2C3D4E5F6")
    assert status == 200 and out["data"]["count"] == 1


async def test_bad_input_is_a_client_error(bank) -> None:
    assert (await _call("GET", "/api/deck/recall/sessions"))[0] == 400
    assert (await _call("GET", "/api/deck/recall/sessions?commit=zz-not-hex"))[0] == 400
    assert (await _call("GET", "/api/deck/recall/sessions?path=core/store.py"))[0] == 422  # relative: no project
    assert (await _call("POST", "/api/deck/recall/links", body={"session": SID}))[0] == 400
    status, _ = await _call("POST", "/api/deck/recall/links",
                            body={"session": "not-a-uuid", "target": r"E:\proj\alpha\a.py", "action": "link"})
    assert status == 422


async def test_link_then_unlink(bank) -> None:
    body = {"session": SID, "target": r"E:\proj\alpha\docs\design.md", "action": "link"}
    status, out = await _call("POST", "/api/deck/recall/links", body=body)
    assert status == 200 and out["data"]["changed"] is True
    assert {"b": "file:alpha/docs/design.md", "rel": "linked"} in bank.links_of(f"cc:{SID}")
    status, out = await _call("POST", "/api/deck/recall/links", body={**body, "action": "unlink"})
    assert status == 200 and out["data"]["changed"] is True
    assert all(lk["b"] != "file:alpha/docs/design.md" for lk in bank.links_of(f"cc:{SID}"))


@pytest.mark.parametrize("origin", ["chrome-extension://abcdef", "moz-extension://abcdef"])
async def test_browser_extensions_are_refused(bank, origin: str) -> None:
    status, out = await _call("GET", "/api/deck/recall/sessions?commit=a1b2c3d", headers={"Origin": origin})
    assert status == 403, out
    status, _ = await _call("POST", "/api/deck/recall/links", headers={"Origin": origin},
                            body={"session": SID, "target": "a1b2c3d", "action": "unlink"})
    assert status == 403
    assert len(bank.links_of(f"cc:{SID}")) == 2  # nothing changed


async def test_remote_requests_are_refused(bank, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("navig.gateway.deck.auth.is_local_request", lambda _r: False)
    assert (await _call("GET", "/api/deck/recall/sessions?commit=a1b2c3d"))[0] == 403


async def test_disabled_recall_says_how_to_enable(bank, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recall_routes, "_enabled", lambda: False)
    status, out = await _call("GET", "/api/deck/recall/sessions?commit=a1b2c3d")
    assert status == 409 and out["enabled"] is False and out["hint"] == "navig recall enable"
