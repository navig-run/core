"""Recall everywhere (S4): the shared queries, the MCP bundle, the narrow stdio server, the
ingest monitor and the deck routes — all over a tiny synthetic bank in an isolated data dir.

No real conversation text and never the real bank: every test builds its bank with the
S1 ``Ingestor`` from the synthetic transcript builders in ``test_recall.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from navig.recall import query
from navig.recall.archive import Archive
from navig.recall.ingest import Ingestor
from navig.recall.model import Evidence, Record, Turn
from navig.recall.projects import ProjectResolver
from navig.recall.store import RecallStore
from navig.recall.vault import Vault

sys.path.insert(0, str(Path(__file__).parent))
from test_recall import CC, rich_session  # noqa: E402

ALPHA = r"E:\proj\alpha"


@pytest.fixture
def bank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A bank with an `alpha` session, a `beta` session and a shared-bucket session."""
    data = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    home = tmp_path / "claude"
    root = data / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"),
                   vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[("alpha", ALPHA), ("beta", r"E:\proj\beta")],
                                            home=r"C:\Users\someone"))
    rich_session().write(home)
    CC(session="22222222-2222-3333-4444-555555555555", cwd=r"E:\proj\beta", prefix="b") \
        .user("Should the OBS bridge use HTTP polling?").say("Polling at 1 Hz is enough for OBS.") \
        .write(home, "E--proj-beta")
    CC(session="33333333-2222-3333-4444-555555555555", cwd=r"C:\Users\someone\scratch", prefix="s") \
        .user("General note about OBS scenes").write(home, "C--Users-someone-scratch")
    rep = ing.run(("claude_code",), claude_homes=[home], codex_home=home / "nocodex")
    assert rep.errors == [] and rep.sources_read == 3
    return ing


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch):
    """Resolve working directories against a fixed registry (never the operator's spaces.json,
    and independent of where pytest puts its temp dirs — those sit inside the navig repo)."""
    r = ProjectResolver(entries=[("alpha", ALPHA), ("beta", r"E:\proj\beta"), ("gamma", r"E:\proj\gamma")],
                        home=r"C:\Users\someone")
    monkeypatch.setattr("navig.recall.projects.project_for_path", r.resolve)
    return r


def _ids(rows: list[Any]) -> set[str]:
    return {r.id if isinstance(r, Record) else r["id"] for r in rows}


# ── query: scope ─────────────────────────────────────────────


def test_scope_defaults_to_the_project_plus_shared() -> None:
    assert query.scope_for("alpha").projects == ("alpha", "shared")
    assert query.scope_for(None, all_projects=True).projects is None
    assert query.scope_for(None, cwd=None).projects == ("shared",)
    assert query.scope_for("shared").projects == ("shared",)


def test_scope_from_a_working_directory_folds_worktrees(resolver) -> None:
    assert query.scope_for(cwd=r"E:\proj\gamma\.dev\worktrees\feat-x").projects == ("gamma", "shared")
    assert query.scope_for(cwd=r"e:\PROJ\beta\src").projects == ("beta", "shared")


def test_search_is_scoped_and_cited(bank) -> None:
    only_alpha = query.search("OBS", scope=query.Scope(("alpha",)), raw=True)
    assert only_alpha["turns"] and {t["project"] for t in only_alpha["turns"]} == {"alpha"}
    alpha_shared = query.search("OBS", scope=query.scope_for("alpha"), raw=True)
    assert {t["project"] for t in alpha_shared["turns"]} == {"alpha", "shared"}
    everything = query.search("OBS", scope=query.Scope.everything(), raw=True, limit=50)
    assert {t["project"] for t in everything["turns"]} == {"alpha", "beta", "shared"}
    for t in everything["turns"]:
        c = t["cite"]
        assert c["ref"] == f"{t['source_id']}#{t['msg_id']}" and c["speaker"] == t["speaker"]
        assert c["owner"] == (t["speaker"] in ("user_typed", "user_answer", "user_plan_verdict"))


def test_ask_returns_the_owner_question_and_the_reply(bank) -> None:
    found = query.ask("HTTP polling", scope=query.scope_for("beta"))
    assert found["asked"], found
    hit = found["asked"][0]
    assert hit["speaker"] == "user_typed" and hit["cite"]["owner"] is True
    assert "1 Hz" in hit["answer"]["text"] and hit["answer"]["cite"]["owner"] is False
    assert not query.ask("HTTP polling", scope=query.scope_for("alpha"))["asked"]


def test_decisions_carry_the_user_basis(bank) -> None:
    recs = query.view("decisions", scope=query.scope_for("alpha"))
    assert recs and all((r.status_basis, r.status) == ("user", "confirmed") for r in recs)


def test_forgotten_ideas_are_unbuilt_unrejected_and_oldest_first(bank) -> None:
    st = bank.store
    for rid, upd, status, impl in (("rk-old", "2026-01-01", "proposed", "not_found"),
                                   ("rk-new", "2026-09-01", "exploring", "unknown"),
                                   ("rk-rej", "2025-01-01", "rejected", "not_found"),
                                   ("rk-built", "2025-02-01", "proposed", "verified")):
        st.upsert_record(Record(id=rid, type="idea", title=rid, project="alpha", status=status, impl=impl,
                                updated=upd, created=upd))
    recs = query.view("forgotten_ideas", scope=query.scope_for("alpha"))
    assert [r.id for r in recs] == ["rk-old", "rk-new"]


def test_unknown_view_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown view"):
        query.view("everything")


def test_turns_page_reads_a_source_in_bounded_cursor_slices(bank) -> None:
    """The export half of the bank contract: the same rows as ``turns_for``, in pages.

    A client ships its own capture to the Brain, and a request is bounded while a bank is
    not, so the upload pages by ``seq``. The cursor must be stable: no row is skipped and
    none repeats when a page size does not divide the source.
    """
    st = bank.store
    sid = "chats:chatgpt:paged"
    st.upsert_source({"source_id": sid, "adapter": "chats", "project": "alpha", "title": "paged"})
    st.add_turns(sid, [Turn(msg_id=f"m{i}", speaker="user_typed", text=f"turn {i}") for i in range(5)])

    pages: list[list[str]] = []
    cursor: int | None = None
    while True:
        turns, cursor = st.turns_page(sid, after_seq=cursor, limit=2)
        if not turns:
            break
        pages.append([turn.msg_id for turn in turns])

    assert pages == [["m0", "m1"], ["m2", "m3"], ["m4"]]
    assert [turn.msg_id for turn in st.turns_for(sid)] == [msg_id for page in pages for msg_id in page]


def test_a_private_chat_never_reaches_a_model(bank) -> None:
    """A chat shared below `agent` level is local-only: turns, excerpts and every record it
    supports are withheld from for_model reads, and shown to local surfaces."""
    st = bank.store
    sid = "chats:chatgpt:private-1"
    st.upsert_source({"source_id": sid, "adapter": "chats", "project": "alpha", "title": "private chat",
                      "meta": {"brain_level": "local"}})
    st.add_turns(sid, [Turn(msg_id="m1", speaker="user_typed", text="zebra launch codename is BLUEFIN")])
    st.upsert_record(Record(id="rk-private", type="decision", title="zebra codename BLUEFIN", project="alpha",
                            status="confirmed", status_basis="user",
                            evidence=[Evidence(source_id=sid, msg_id="m1", speaker="user_typed",
                                               excerpt="zebra launch codename is BLUEFIN")]))
    scope = query.scope_for("alpha")
    assert "rk-private" in _ids(query.search("zebra", scope=scope)["records"])
    assert query.search("zebra", scope=scope, raw=True)["turns"]
    model = query.search("zebra", scope=scope, for_model=True)
    assert not model["records"] and not model["turns"]
    assert not query.search("zebra", scope=scope, raw=True, for_model=True)["turns"]
    assert query.record_detail("rk-private") is not None
    assert query.record_detail("rk-private", for_model=True) is None
    assert query.excerpt(sid, "m1") is not None
    assert query.excerpt(sid, "m1", for_model=True) is None
    assert "rk-private" not in _ids(query.view("decisions", scope=scope, for_model=True))
    # an agent-level share may leave
    st.upsert_source({"source_id": sid, "adapter": "chats", "project": "alpha", "meta": {"brain_level": "agent"}})
    assert query.record_detail("rk-private", for_model=True) is not None


def test_search_all_recall_source_withholds_private_records(bank) -> None:
    """``search_all``'s recall source feeds the model, so it inherits the ``for_model``
    contract: a record backed by a below-agent chat never reaches its rows, while local
    (non-model) surfaces still see it. Regression net for the raw-store bypass fixed
    2026-10-19."""
    from navig.agent.tools import search_all_tool as sa

    st = bank.store
    sid = "chats:chatgpt:private-search"
    st.upsert_source({"source_id": sid, "adapter": "chats", "project": "alpha", "title": "private chat",
                      "meta": {"brain_level": "local"}})
    st.add_turns(sid, [Turn(msg_id="m1", speaker="user_typed", text="walrus budget codename is SEVENTEEN")])
    st.upsert_record(Record(id="rk-walrus", type="decision", title="walrus budget SEVENTEEN", project="alpha",
                            status="confirmed", status_basis="user",
                            evidence=[Evidence(source_id=sid, msg_id="m1", speaker="user_typed",
                                               excerpt="walrus budget codename is SEVENTEEN")]))
    st.upsert_record(Record(id="rk-walrus-public", type="finding", title="walrus public finding", project="alpha",
                            status="confirmed", status_basis="user"))

    rows, status = asyncio.run(sa._recall("walrus", 5))
    assert status == "ok"
    refs = {r["ref"] for r in rows}
    assert "recall:rk-walrus-public" in refs
    assert "recall:rk-walrus" not in refs
    # the same record stays visible to local surfaces
    assert "rk-walrus" in _ids(query.search("walrus", scope=query.scope_for("alpha"))["records"])


def test_record_detail_cites_every_piece_of_evidence(bank) -> None:
    rid = query.view("decisions", scope=query.Scope.everything())[0].id
    d = query.record_detail(rid)
    assert d["record"]["id"] == rid and d["evidence"] and d["evidence_total"] == len(d["evidence"])
    for e in d["evidence"]:
        assert e["cite"]["ref"] == f"{e['source_id']}#{e['msg_id']}"
        ex = query.excerpt(e["source_id"], e["msg_id"], before=1, after=1)
        assert ex and [t for t in ex["turns"] if t["focus"]][0]["msg_id"] == e["msg_id"]


# ── MCP bundle ───────────────────────────────────────────────


def _server():
    from navig.mcp.tools import recall as bundle

    server = SimpleNamespace(tools={}, _tool_handlers={})
    bundle.register(server)
    return server


def _call(server, name: str, **args: Any) -> Any:
    return server._tool_handlers[name](server, args)


def test_bundle_registers_eleven_classified_tools() -> None:
    s = _server()
    assert set(s.tools) == set(s._tool_handlers) == set(s._tool_safety)
    assert len(s.tools) == 11
    assert s._tool_safety["recall_ingest"] == "moderate"
    assert {v for k, v in s._tool_safety.items() if k != "recall_ingest"} == {"safe"}
    for name, spec in s.tools.items():
        if name != "recall_ingest":
            assert "status_basis" in spec["description"] and "owner" in spec["description"], name


def test_mcp_tools_default_to_the_callers_project(bank, resolver, monkeypatch) -> None:
    monkeypatch.setattr("navig.platform.paths.invocation_cwd", lambda: Path(r"E:\proj\beta\src"))
    s = _server()
    out = _call(s, "recall_search", query="OBS", raw=True)
    assert out["scope"] == {"projects": ["beta", "shared"]}
    assert {t["project"] for t in out["turns"]} <= {"beta", "shared"} and out["turns"]
    wide = _call(s, "recall_search", query="OBS", raw=True, all_projects=True, limit=50)
    assert wide["scope"] == {"projects": "all"} and "alpha" in {t["project"] for t in wide["turns"]}
    named = _call(s, "recall_decisions", project="alpha")
    assert named["records"] and all(r["status_basis"] == "user" for r in named["records"])


def test_mcp_get_timeline_related_excerpt(bank, resolver, monkeypatch) -> None:
    monkeypatch.setattr("navig.platform.paths.invocation_cwd", lambda: Path(ALPHA))
    s = _server()
    rid = _call(s, "recall_decisions", all_projects=True)["records"][0]["id"]
    got = _call(s, "recall_get", id=rid)
    assert got["record"]["id"] == rid and got["evidence"][0]["cite"]["owner"] is True
    assert _call(s, "recall_timeline", id=rid)["events"]
    assert "related" in _call(s, "recall_related", id=rid)
    ev = got["evidence"][0]
    ex = _call(s, "recall_source_excerpt", source_id=ev["source_id"], msg_id=ev["msg_id"])
    assert any(t["focus"] for t in ex["turns"])
    assert _call(s, "recall_get", id="rk-nope")["isError"] is True
    assert _call(s, "recall_search", query="")["isError"] is True
    tables = _call(s, "recall_tables", all_projects=True)
    assert tables["records"] and "| obs-websocket | free |" in tables["records"][0]["body"]


def test_mcp_ingest_refuses_until_reading_is_enabled(bank, monkeypatch) -> None:
    monkeypatch.setattr(query, "is_enabled", lambda: False)
    out = _call(_server(), "recall_ingest")
    assert out["isError"] is True and "navig recall enable" in out["error"]


def test_mcp_hint_when_the_bank_is_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr(query, "is_enabled", lambda: False)
    out = _call(_server(), "recall_search", query="anything", all_projects=True)
    assert not out["records"] and "navig recall enable" in out["hint"]


def test_only_filters_bundles_and_rejects_typos() -> None:
    from navig.mcp.tools import register_all_tools

    s = SimpleNamespace(tools={}, _tool_handlers={})
    register_all_tools(s, only=["recall"])
    assert s.tools and all(n.startswith("recall_") for n in s.tools)
    with pytest.raises(ValueError, match="unknown MCP tool bundle"):
        register_all_tools(SimpleNamespace(tools={}, _tool_handlers={}), only=["recal"])
    full = SimpleNamespace(tools={}, _tool_handlers={})
    register_all_tools(full)
    assert set(s.tools) < set(full.tools)


def test_narrow_stdio_server_answers_with_citations(bank, tmp_path: Path) -> None:
    """End to end over a real process: initialize → tools/list shows only recall tools →
    tools/call recall_search returns cited results scoped to the client's project."""
    import shutil
    import tempfile

    import navig

    # The client's workspace: outside the navig repo (pytest's temp dirs live inside it and
    # would resolve to "navig"), registered as project "alpha" in an isolated config dir.
    repo = Path(tempfile.mkdtemp(prefix="navig-recall-alpha-"))
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "spaces.json").write_text(json.dumps({"version": 1, "active": None,
                                                 "spaces": [{"id": "alpha", "path": str(repo)}]}),
                                     encoding="utf-8")
    core = str(Path(navig.__file__).resolve().parents[1])
    env = {**os.environ, "NAVIG_DATA_DIR": os.environ["NAVIG_DATA_DIR"],
           "NAVIG_CONFIG_DIR": str(cfg), "PYTHONPATH": core, "PYTHONIOENCODING": "utf-8"}
    env.pop("NAVIG_INVOCATION_CWD", None)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "recall_search", "arguments": {"query": "Telegram controller", "raw": True}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "recall_ask", "arguments": {"question": "Telegram controller for OBS"}}},
    ]
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "navig", "mcp", "serve", "--transport", "stdio", "--only", "recall"],
            input="".join(json.dumps(m) + "\n" for m in msgs).encode(), capture_output=True,
            env=env, cwd=str(repo), timeout=240)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    replies = {r["id"]: r for r in map(json.loads, lines)}  # every stdout line must be JSON-RPC
    assert set(replies) == {1, 2, 3, 4}, proc.stderr.decode("utf-8", "replace")[-2000:]
    names = {t["name"] for t in replies[2]["result"]["tools"]}
    assert names and all(n.startswith("recall_") for n in names) and "recall_ask" in names
    res = replies[3]["result"]
    assert not res.get("isError"), res
    payload = json.loads(res["content"][0]["text"])
    assert payload["scope"] == {"projects": ["alpha", "shared"]}
    assert payload["turns"], payload
    first = payload["turns"][0]
    assert first["project"] == "alpha" and first["cite"]["source_id"].startswith("cc:")
    assert first["cite"]["speaker"] == "user_typed" and first["cite"]["owner"] is True
    asked = json.loads(replies[4]["result"]["content"][0]["text"])
    assert asked["asked"], asked
    hit = asked["asked"][0]
    assert hit["cite"]["owner"] is True and hit["answer"]["cite"]["owner"] is False
    assert hit["answer"]["cite"]["ref"].startswith(hit["source_id"] + "#")


# ── install-config --client claude-code ──────────────────────


def test_claude_code_command_serves_only_recall_over_stdio() -> None:
    from navig.commands.mcp_cmd import claude_code_command

    cmd = claude_code_command("user", "claude")
    assert cmd[:7] == ["claude", "mcp", "add", "navig-recall", "--scope", "user", "--"]
    assert cmd[-6:] == ["mcp", "serve", "--transport", "stdio", "--only", "recall"]


def test_install_config_dry_run_changes_nothing(monkeypatch) -> None:
    from typer.testing import CliRunner

    from navig.commands import mcp_cmd

    ran: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    res = CliRunner().invoke(mcp_cmd.mcp_app, ["install-config", "--client", "claude-code", "--scope", "project",
                                               "--dry-run"])
    assert res.exit_code == 0, res.output
    out = " ".join(res.output.split())
    assert "mcp add navig-recall --scope project --" in out and "--only recall" in out
    assert ran == []


def test_install_config_without_a_terminal_needs_yes(monkeypatch) -> None:
    from typer.testing import CliRunner

    from navig.commands import mcp_cmd

    ran: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr("shutil.which", lambda name: f"C:/bin/{name}.exe")
    res = CliRunner().invoke(mcp_cmd.mcp_app, ["install-config", "--client", "claude-code"])
    assert res.exit_code == 2 and ran == []


# ── navig recall enable --auto ───────────────────────────────


def test_enable_auto_turns_on_reading_and_the_monitor(monkeypatch) -> None:
    from typer.testing import CliRunner

    from navig.commands import recall as cmd

    written: dict[str, Any] = {}
    fake = SimpleNamespace(set_global=lambda k, v: written.__setitem__(k, v))
    monkeypatch.setattr("navig.config.get_config_manager", lambda: fake)
    res = CliRunner().invoke(cmd.recall_app, ["enable", "--auto"])
    assert res.exit_code == 0, res.output
    assert written == {"recall.read_local": True, "monitors.recall_ingest.enabled": True}
    written.clear()
    assert CliRunner().invoke(cmd.recall_app, ["enable"]).exit_code == 0
    assert written == {"recall.read_local": True}


# ── monitor ──────────────────────────────────────────────────


@pytest.fixture
def mon(monkeypatch):
    from navig.notify.monitors import recall_ingest as m

    monkeypatch.setitem(m._state, "failures", 0)
    monkeypatch.setitem(m._state, "reported", False)
    monkeypatch.setattr(m, "_reading_allowed", lambda: True)
    return m


def test_monitor_skips_when_reading_is_off(mon, monkeypatch) -> None:
    monkeypatch.setattr(mon, "_reading_allowed", lambda: False)
    monkeypatch.setattr(mon, "_ingest", lambda: pytest.fail("must not ingest"))
    assert mon.sweep_once() == {"skipped": "disabled"}


def test_monitor_skips_while_a_pass_is_running(mon, monkeypatch) -> None:
    monkeypatch.setattr(mon, "_ingest", lambda: pytest.fail("must not ingest twice"))
    assert mon._busy.acquire(blocking=False)
    try:
        assert mon.sweep_once() == {"skipped": "busy"}
    finally:
        mon._busy.release()


def test_monitor_never_raises_and_reports_a_failure_streak_once(mon, monkeypatch) -> None:
    from navig.core import incidents

    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(mon, "_ingest", boom)
    for _ in range(mon.FAIL_STREAK + 2):
        assert mon.sweep_once()["skipped"] == "error"
    assert [ev for ev, _ in recorded] == [incidents.RECALL_INGEST_FAILING]
    assert incidents.RECALL_INGEST_FAILING in incidents.DESCRIPTIONS
    monkeypatch.setattr(mon, "_ingest", lambda: {"sources_read": 1, "turns_added": 2, "errors": []})
    assert mon.sweep_once()["turns_added"] == 2
    assert mon._state == {"failures": 0, "reported": False}


def test_monitor_ingests_a_new_session(bank, mon, monkeypatch) -> None:
    """The real path: a session written after the bank was built is searchable after one pass."""
    home = Path(os.environ["NAVIG_DATA_DIR"]).parent / "claude"
    CC(session="44444444-2222-3333-4444-555555555555", cwd=ALPHA, prefix="n") \
        .user("Brand new idea: a walrus dashboard").write(home)
    monkeypatch.setattr(mon, "_ingest", lambda: bank.run(("claude_code",), claude_homes=[home],
                                                          codex_home=home / "x").to_dict())
    rep = mon.sweep_once()
    assert rep["turns_added"] == 1 and rep["unchanged"] == 3
    assert query.search("walrus", scope=query.scope_for("alpha"))["turns"]


def test_monitor_is_wired_default_off_with_a_sample() -> None:
    from navig.gateway.deck.routes.notify import _DEFAULT_ON, _MONITOR_KEYS
    from navig.gateway.server import NavigGateway
    from navig.notify.producers.samples import _SAMPLES

    assert "recall_ingest" in NavigGateway.MONITOR_KEYS and "recall_ingest" in _MONITOR_KEYS
    assert "recall_ingest" not in NavigGateway.MONITORS_DEFAULT_ON and "recall_ingest" not in _DEFAULT_ON
    assert "recall_ingest" in _SAMPLES


def test_monitor_sweep_runs_off_the_event_loop() -> None:
    import ast
    import inspect

    from navig.notify.monitors import recall_ingest as m

    tree = ast.parse(inspect.getsource(m.run_recall_ingest))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert not [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "sweep_once"]
    assert [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "to_thread"
            and any(isinstance(a, ast.Name) and a.id == "sweep_once" for a in c.args)]


def test_gateway_starts_the_monitor_by_name(monkeypatch) -> None:
    from navig.gateway.server import NavigGateway

    spawned: list[str] = []
    gw = NavigGateway.__new__(NavigGateway)
    gw._monitor_tasks = {}

    def _spawn(name, coro):
        coro.close()
        spawned.append(name)

    gw._spawn_monitor_task = _spawn
    gw._start_monitor("recall_ingest")
    assert spawned == ["recall_ingest"]


# ── deck routes ──────────────────────────────────────────────


@pytest.fixture
async def client(bank, monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import recall as routes

    monkeypatch.setattr(routes, "_enabled", lambda: True)

    app = web.Application()
    routes.register(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


async def _get(client, path: str) -> tuple[int, dict]:
    r = await client.get(path)
    return r.status, await r.json()


async def test_deck_status_and_search(client) -> None:
    st, body = await _get(client, "/api/deck/recall/status")
    assert st == 200 and body["ok"] and body["data"]["records"] > 0 and "alpha" in body["data"]["projects"]
    st, body = await _get(client, "/api/deck/recall/search?q=OBS&project=alpha")
    assert st == 200 and body["data"]["records"] and all(r["project"] == "alpha" for r in body["data"]["records"])
    st, body = await _get(client, "/api/deck/recall/search?q=")
    assert st == 400 and body["ok"] is False


async def test_deck_views_record_excerpt(client) -> None:
    st, body = await _get(client, "/api/deck/recall/views/decisions")
    assert st == 200 and body["data"]["count"] >= 1
    rid = body["data"]["records"][0]["id"]
    assert "body" not in body["data"]["records"][0]
    st, body = await _get(client, f"/api/deck/recall/record?id={rid}")
    assert st == 200 and body["data"]["evidence"]
    ev = body["data"]["evidence"][0]
    st, body = await _get(client, f"/api/deck/recall/excerpt?source={ev['source_id']}&msg={ev['msg_id']}")
    assert st == 200 and any(t["focus"] for t in body["data"]["turns"])
    assert (await _get(client, "/api/deck/recall/views/nope"))[0] == 404
    assert (await _get(client, "/api/deck/recall/record?id=rk-nope"))[0] == 404
    st, body = await _get(client, "/api/deck/recall/ask?q=HTTP%20polling")
    assert st == 200 and body["data"]["asked"]


def test_deck_bank_routes_are_read_only() -> None:
    # The only write route in the module is the session-links one (link/unlink a session to a
    # file, plan or commit); no route writes knowledge records.
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import recall as routes

    app = web.Application()
    routes.register(app)
    writes = {r.resource.canonical for r in app.router.routes() if r.method not in ("GET", "HEAD")}
    assert writes == {"/api/deck/recall/links"}, writes


async def test_deck_recall_is_local_only_and_off_until_enabled(bank, monkeypatch) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import recall as routes

    monkeypatch.setattr(routes, "_enabled", lambda: False)
    app = web.Application()
    routes.register(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        r = await c.get("/api/deck/recall/search?q=OBS")
        assert r.status == 409 and (await r.json())["enabled"] is False
        assert (await c.get("/api/deck/recall/status")).status == 200  # a surface can show "off"
        monkeypatch.setattr(routes, "_enabled", lambda: True)
        r = await c.get("/api/deck/recall/search?q=OBS", headers={"Origin": "chrome-extension://abc"})
        assert r.status == 403  # a Lens / extension never reads Recall
    finally:
        await c.close()
