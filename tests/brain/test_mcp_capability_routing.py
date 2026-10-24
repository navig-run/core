"""Capability routing: which machine owns a tool, and what happens when the Brain is down.

These are the tests the mission's acceptance criteria hang on:

* a linked client's memory/recall calls never touch a local database;
* an unreachable Brain is an ERROR, not a silent local read or write;
* an unreadable link is refused, not guessed at;
* a genuinely local capability (files, shell, browser) is never sent to the Brain, even on a
  linked machine.

The suite is hermetic: the link file, the config dir and the data dir are all redirected
into ``tmp_path``, so it neither reads nor writes the operator's real Brain or stores.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

BRAIN_URL = "https://brain.example.invalid"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A private home for the link, the config and the stores — and no cached role."""
    from navig.mcp import capability

    link_file = tmp_path / "brain-link.json"
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NAVIG_BRAIN_LINK_FILE", str(link_file))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    monkeypatch.delenv("NAVIG_BRAIN_ROUTE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_TIMEOUT", raising=False)
    capability.reload()
    yield {"link": link_file, "cfg": cfg, "data": data}
    capability.reload()


def _write_link(path: Path, *, url: str = BRAIN_URL, token: str = "tok-secret-123") -> Path:
    path.write_text(
        json.dumps(
            {
                "url": url,
                "brainName": "outpost-server",
                "deviceId": "83d011d16b16",
                "token": token,
                "linkedAt": 1792534071980,
                "version": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _set_role(cfg: Path, role: str) -> None:
    (cfg / "config.yaml").write_text(f"brain:\n  role: {role}\n", encoding="utf-8")


# ── the contract itself ───────────────────────────────────────


def test_brain_owned_families_are_declared_as_such():
    from navig.mcp import capability

    for tool in (
        "memory_key_facts_remember",
        "memory_key_facts_retrieve",
        "memory_key_facts_stats",
        "recall_search",
        "recall_ingest",
    ):
        contract = capability.capability_for(tool)
        assert contract.data_authority == capability.AUTHORITY_BRAIN, tool
        assert contract.offline_policy == capability.OFFLINE_FAIL, tool
        assert contract.risk == "high", tool


def test_every_registered_mcp_tool_resolves_to_a_contract():
    """No tool may reach a handler without an ownership answer — including a new one."""
    from navig.mcp import capability
    from navig.mcp.tools import all_bundles

    server = _Server()
    for bundle in all_bundles():
        register = getattr(bundle, "register", None)
        if register is not None:
            register(server)
    assert len(server.tools) >= 15, "the catalogue did not register; the assertion below is vacuous"
    for name in server.tools:
        contract = capability.capability_for(name)
        assert contract.data_authority in (capability.AUTHORITY_BRAIN, capability.AUTHORITY_LOCAL)
        assert contract.execution_target in ("local", "brain", "either")
        assert contract.offline_policy in (
            capability.OFFLINE_FAIL,
            capability.OFFLINE_DEGRADE,
            capability.OFFLINE_LOCAL,
        )


# ── routing decisions ─────────────────────────────────────────


def test_local_capability_never_leaves_the_machine(_isolated):
    from navig.mcp import capability

    _write_link(_isolated["link"])
    _set_role(_isolated["cfg"], "client")
    capability.reload()

    for tool in ("system_run_command", "filesystem_read", "cdp_navigate", "dock_snapshot"):
        route = capability.resolve(tool)
        assert route.is_local, f"{tool} must run locally on a linked client"
        assert route.client is None


def test_linked_client_routes_memory_and_recall_to_the_brain(_isolated):
    from navig.mcp import capability

    _write_link(_isolated["link"])
    _set_role(_isolated["cfg"], "client")
    capability.reload()

    for tool in ("memory_key_facts_retrieve", "recall_search"):
        route = capability.resolve(tool)
        assert route.is_brain, tool
        assert route.client is not None
        assert route.client.link.base_url == BRAIN_URL


def test_brain_role_without_a_link_is_local(_isolated):
    from navig.mcp import capability

    _set_role(_isolated["cfg"], "brain")
    capability.reload()

    route = capability.resolve("memory_key_facts_stats")
    assert route.is_local, "on the Brain the local store IS the authority"


def test_client_role_without_a_link_is_refused_not_served_locally(_isolated):
    from navig.mcp import capability

    _set_role(_isolated["cfg"], "client")
    capability.reload()

    route = capability.resolve("memory_key_facts_remember")
    assert route.is_refused
    assert route.refusal["reason"] == "no_brain_link"
    assert route.refusal["wrote"] is False


def test_corrupt_link_is_refused(_isolated):
    from navig.mcp import capability

    _isolated["link"].write_text('{"url": "https://brain', encoding="utf-8")
    _set_role(_isolated["cfg"], "all-in-one")
    capability.reload()

    route = capability.resolve("memory_key_facts_retrieve")
    assert route.is_refused
    assert route.refusal["reason"] == "link_unreadable"
    assert "nothing was read or written locally" in route.refusal["error"].lower()


def test_incomplete_link_is_refused(_isolated):
    from navig.mcp import capability

    _isolated["link"].write_text(json.dumps({"url": BRAIN_URL}), encoding="utf-8")
    _set_role(_isolated["cfg"], "all-in-one")
    capability.reload()

    route = capability.resolve("memory_key_facts_stats")
    assert route.is_refused
    assert route.refusal["reason"] == "link_unreadable"


def test_env_override_can_force_local(_isolated, monkeypatch: pytest.MonkeyPatch):
    from navig.mcp import capability

    _write_link(_isolated["link"])
    _set_role(_isolated["cfg"], "client")
    monkeypatch.setenv(capability.ROUTE_ENV, "local")
    capability.reload()

    assert capability.resolve("memory_key_facts_stats").is_local


# ── the tool handlers, end to end ─────────────────────────────


class _Server:
    """The minimum surface a tool bundle's ``register`` fills."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self._tool_handlers: dict[str, Any] = {}
        self._tool_safety: dict[str, str] = {}


def _servers() -> tuple[_Server, _Server]:
    from navig.mcp.tools import memory, recall

    memory_server, recall_server = _Server(), _Server()
    memory.register(memory_server)
    recall.register(recall_server)
    return memory_server, recall_server


def test_unreachable_brain_returns_an_error_and_writes_nothing(_isolated):
    """The acceptance criterion: no silent local write when the Brain is down."""
    from navig.memory.key_facts import get_key_facts_db_path

    _write_link(_isolated["link"], url="http://127.0.0.1:9")  # discard port: nothing listens
    _set_role(_isolated["cfg"], "client")

    memory_server, _ = _servers()
    result = memory_server._tool_handlers["memory_key_facts_remember"](
        None, {"fact": "the Brain owns this row", "source": "test"}
    )

    assert result["isError"] is True
    assert result["route"] == "brain"
    assert result["wrote"] is False
    assert result["reason"] in ("unreachable", "timeout", "http_error")
    assert not get_key_facts_db_path().exists(), "a refused route must not create a local store"


def test_linked_client_reads_facts_from_the_brain_not_the_local_store(_isolated):
    """A stub Brain answers; the local store must still not exist."""
    from navig.memory.key_facts import get_key_facts_db_path

    seen: list[tuple[str, dict]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server's spelling
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            seen.append((self.path, body))
            assert self.headers.get("Authorization", "").startswith("Bearer ")
            payload = json.dumps(
                {"ok": True, "data": [{"id": "mem-1", "content": "from the brain"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: A003 — silence the test server
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _write_link(_isolated["link"], url=f"http://127.0.0.1:{server.server_port}")
        _set_role(_isolated["cfg"], "client")

        memory_server, _ = _servers()
        result = memory_server._tool_handlers["memory_key_facts_retrieve"](
            None, {"query": "brain", "limit": 5}
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result == [{"id": "mem-1", "content": "from the brain"}]
    assert seen and seen[0][0] == "/api/deck/brain/data/memory/retrieve"
    assert seen[0][1] == {"query": "brain", "limit": 5}
    assert not get_key_facts_db_path().exists(), "the client must not open a local key-fact store"


def test_brain_role_writes_locally_as_before(_isolated):
    """The Brain keeps today's behaviour: the same handler, the same store."""
    from navig.memory.key_facts import get_key_facts_db_path

    _set_role(_isolated["cfg"], "brain")

    memory_server, _ = _servers()
    result = memory_server._tool_handlers["memory_key_facts_remember"](
        None, {"fact": "a fact the Brain itself learned", "source": "test"}
    )

    assert result.get("isError") is not True
    assert result["status"] == "stored"
    assert get_key_facts_db_path().exists()


def test_client_ingest_uploads_its_own_rows_to_the_brain(_isolated, monkeypatch):
    """Ingest captures here (the transcripts are this machine's) and ships rows to the bank.

    The provenance travels with the rows: source path, adapter and mtime, plus the turn ids,
    so the Brain can cite what it received instead of a claim that something was ingested.
    """
    import socket

    from navig.recall import ingest as ingest_module
    from navig.recall import model
    from navig.recall import sources as sources_module
    from navig.recall import store as store_module

    seen: list[tuple[str, dict]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server's spelling
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            seen.append((self.path, body))
            payload = json.dumps(
                {
                    "ok": True,
                    "data": {"sources": 1, "turns_added": 1, "machine": body.get("machine")},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: A003 — silence the test server
            return

    class _Store:
        def __init__(self) -> None:
            self.sources = [
                {
                    "source_id": "src-1",
                    "adapter": "claude_code",
                    "path": "/home/neuron/.claude/session.jsonl",
                    "mtime": 1.0,
                    "cursor": "",
                }
            ]

        def list_sources(self, **_kwargs):
            return [dict(source) for source in self.sources]

        def turns_page(self, source_id: str, *, after_seq: int | None = None, limit: int = 500):
            assert source_id == "src-1"
            if after_seq is not None:
                return [], after_seq
            return [model.Turn(msg_id="m1", speaker="user_typed", text="hello from the neuron")], 1

    class _Report:
        errors: list = []

        def to_dict(self):
            return {"sources_seen": 1, "turns_added": 1}

    store = _Store()

    class _Ingestor:
        def __init__(self, store=None, **_kwargs):  # noqa: A002 — mirrors the real signature
            self.store = store

        def run(self, adapters):
            assert list(adapters) == ["claude_code"]
            self.store.sources[0]["mtime"] = 2.0  # the capture moved the source
            return _Report()

    monkeypatch.setattr(sources_module, "ADAPTERS", ("claude_code",))
    monkeypatch.setattr(ingest_module, "Ingestor", _Ingestor)
    monkeypatch.setattr(store_module, "get_recall_store", lambda: store)

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _write_link(_isolated["link"], url=f"http://127.0.0.1:{server.server_port}")
        _set_role(_isolated["cfg"], "client")
        _, recall_server = _servers()
        result = recall_server._tool_handlers["recall_ingest"](None, {"project": "demo"})
    finally:
        server.shutdown()
        server.server_close()

    assert result.get("isError") is not True
    assert result["ok"] is True
    assert result["upload"] == {
        "sources": 1,
        "turns_sent": 1,
        "turns_added": 1,
        "machine": socket.gethostname(),
    }
    assert seen and seen[0][0] == "/api/deck/brain/data/recall/ingest"
    path, body = seen[0]
    assert body["machine"] == socket.gethostname()
    assert body["sources"][0]["path"] == "/home/neuron/.claude/session.jsonl"
    assert body["sources"][0]["adapter"] == "claude_code"
    assert body["turns"]["src-1"][0]["msg_id"] == "m1"
    assert body["turns"]["src-1"][0]["speaker"] == "user_typed"


def test_client_ingest_pages_a_large_source(_isolated, monkeypatch):
    """A bank of any size crosses in bounded requests, never one enormous body.

    The relay carries bodies as text and ends a request after thirty seconds, so the upload
    slices a source and reports what actually crossed. Sending a page twice costs nothing:
    the Brain dedupes turns on ``(source_id, msg_id)``.
    """
    from navig.mcp.tools import recall as recall_module
    from navig.recall import ingest as ingest_module
    from navig.recall import model
    from navig.recall import sources as sources_module
    from navig.recall import store as store_module

    bodies: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server's spelling
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            bodies.append(body)
            _, turns = next(iter(body["turns"].items()))
            payload = json.dumps(
                {"ok": True, "data": {"sources": 1, "turns_added": len(turns)}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: A003 — silence the test server
            return

    page = recall_module.TURN_PAGE_SIZE
    total = page * 2 + 7
    every_turn = [
        model.Turn(msg_id=f"m{i}", speaker="user_typed", text=f"turn {i}") for i in range(total)
    ]

    class _Store:
        sources = [
            {"source_id": "src-1", "adapter": "claude_code", "path": "/p.jsonl", "mtime": 1.0}
        ]

        def list_sources(self, **_kwargs):
            return [dict(source) for source in self.sources]

        def turns_page(self, source_id: str, *, after_seq: int | None = None, limit: int = 500):
            assert source_id == "src-1"
            start = int(after_seq or 0)
            chunk = every_turn[start : start + int(limit)]
            return chunk, start + len(chunk)

    store = _Store()

    class _Report:
        errors: list = []

        def to_dict(self):
            return {"sources_seen": 1, "turns_added": total}

    class _Ingestor:
        def __init__(self, store=None, **_kwargs):  # noqa: A002 — mirrors the real signature
            self.store = store

        def run(self, adapters):
            assert list(adapters) == ["claude_code"]
            self.store.sources[0]["mtime"] = 2.0
            return _Report()

    monkeypatch.setattr(sources_module, "ADAPTERS", ("claude_code",))
    monkeypatch.setattr(ingest_module, "Ingestor", _Ingestor)
    monkeypatch.setattr(store_module, "get_recall_store", lambda: store)

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _write_link(_isolated["link"], url=f"http://127.0.0.1:{server.server_port}")
        _set_role(_isolated["cfg"], "client")
        _, recall_server = _servers()
        result = recall_server._tool_handlers["recall_ingest"](None, {"project": "demo"})
    finally:
        server.shutdown()
        server.server_close()

    assert result.get("isError") is not True
    assert len(bodies) == 3, f"expected three pages, got {len(bodies)}"
    assert [len(body["turns"]["src-1"]) for body in bodies] == [page, page, 7]
    assert bodies[0]["turns"]["src-1"][0]["msg_id"] == "m0"
    assert bodies[2]["turns"]["src-1"][-1]["msg_id"] == f"m{total - 1}"
    assert result["upload"]["sources"] == 1
    assert result["upload"]["turns_sent"] == total
    assert result["upload"]["turns_added"] == total


def test_client_ingest_keeps_the_capture_when_the_upload_fails(_isolated, monkeypatch):
    """A Brain that is down must cost the upload, never the capture made on this machine."""
    from navig.recall import ingest as ingest_module
    from navig.recall import sources as sources_module
    from navig.recall import store as store_module

    class _Store:
        sources = [
            {"source_id": "src-1", "adapter": "claude_code", "path": "/p.jsonl", "mtime": 1.0}
        ]

        def list_sources(self, **_kwargs):
            return [dict(source) for source in self.sources]

        def turns_page(self, source_id: str, *, after_seq: int | None = None, limit: int = 500):
            if after_seq is not None:
                return [], after_seq
            from navig.recall import model

            return [model.Turn(msg_id="m1", speaker="user_typed", text="kept here")], 1

    store = _Store()

    class _Report:
        errors: list = []

        def to_dict(self):
            return {"sources_seen": 1, "turns_added": 1}

    class _Ingestor:
        def __init__(self, store=None, **_kwargs):
            self.store = store

        def run(self, adapters):
            self.store.sources[0]["mtime"] = 2.0
            return _Report()

    monkeypatch.setattr(sources_module, "ADAPTERS", ("claude_code",))
    monkeypatch.setattr(ingest_module, "Ingestor", _Ingestor)
    monkeypatch.setattr(store_module, "get_recall_store", lambda: store)

    _write_link(_isolated["link"], url="http://127.0.0.1:9")  # discard port
    _set_role(_isolated["cfg"], "client")

    _, recall_server = _servers()
    result = recall_server._tool_handlers["recall_ingest"](None, {"project": "demo"})

    assert result["isError"] is True
    assert result["ok"] is False
    assert result["captured_local"] is True
    assert result["upload"]["turns_sent"] == 0, "nothing crossed, so nothing may be reported"
    assert result["upload"]["sources"] == 0
    assert "next ingest re-sends it" in result["error"]


def test_route_payloads_never_carry_the_link_token(_isolated):
    """A refusal is logged and shown to a model; the per-machine token must not be in it."""
    from navig.mcp import capability

    _write_link(_isolated["link"], token="super-secret-token")
    _set_role(_isolated["cfg"], "client")
    route = capability.resolve("recall_search")
    assert route.is_brain
    blob = json.dumps(route.refusal) + json.dumps(route.link_state.link.to_public_dict())
    assert "super-secret-token" not in blob
    # ... while still being identifiable for support.
    assert route.link_state.link.token_fingerprint in blob


# ── the two other answers the same file must give ─────────────


def test_an_incomplete_link_still_blocks_a_local_brain_start(_isolated):
    """A URL with no token says "my brain is elsewhere"; that claim alone is enough.

    The data path refuses it (no token, cannot authenticate). The START path must also
    refuse, or the machine mints the second authority that the incomplete link was trying
    to declare.
    """
    from navig.brain.link import read_brain_link_state

    _isolated["link"].write_text(json.dumps({"url": BRAIN_URL}), encoding="utf-8")
    state = read_brain_link_state(_isolated["link"])

    assert state.state == "incomplete"
    assert state.is_usable is False, "it cannot authenticate, so it must not carry writes"
    assert state.blocks_start is True
    assert state.as_link_dict()["brainName"] == "" and state.as_link_dict()["__incomplete__"]


def test_a_corrupt_link_never_blocks_a_start(_isolated):
    """One truncated write must not strand a machine (the 2026-10-22 boot loop)."""
    from navig.brain.link import read_brain_link_state

    _isolated["link"].write_text('{"url": "https://brain', encoding="utf-8")
    state = read_brain_link_state(_isolated["link"])

    assert state.state == "corrupt"
    assert state.blocks_start is False
    assert state.as_link_dict() == {"__corrupt__": True}


def test_declared_local_families_carry_a_reason():
    """A family kept local is a decision, so it states one — silence reads as "nobody looked".

    ``runtime`` and ``agent`` are the interesting ones: the architecture puts goals and
    scheduling on the Brain, so keeping them host-local is a recorded gap, not an oversight.
    """
    from navig.mcp import capability

    for contract in capability.contracts().values():
        assert contract.reason, f"{contract.family} has no recorded reason"
    for family in ("runtime", "agent"):
        contract = capability.capability_for(f"{family}_example")
        assert contract.data_authority == capability.AUTHORITY_LOCAL
        assert "phase C" in contract.reason, family
