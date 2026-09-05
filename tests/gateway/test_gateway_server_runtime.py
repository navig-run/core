from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

pytestmark = pytest.mark.integration


class DummyRequest:
    def __init__(
        self,
        *,
        payload=None,
        json_exc: Exception | None = None,
        query: dict | None = None,
        match_info: dict | None = None,
        path: str = "/",
        method: str = "GET",
        can_read_body: bool = True,
        headers: dict | None = None,
    ):
        self._payload = payload if payload is not None else {}
        self._json_exc = json_exc
        self.query = query or {}
        self.match_info = match_info or {}
        self.path = path
        self.method = method
        self.can_read_body = can_read_body
        self.headers = headers or {}

    async def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


def response_json(resp: web.Response) -> dict:
    return json.loads(resp.text)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from navig.gateway import server as sv

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    cm = SimpleNamespace(
        global_config={
            "gateway": {"enabled": True, "port": 8789, "host": "127.0.0.1"},
            "heartbeat": {},
            "mesh": {"enabled": False},
            "agents": {},
            "cron": {},
        },
        global_config_dir=config_dir,
    )
    monkeypatch.setattr(sv, "get_config_manager", lambda: cm)

    gw = sv.NavigGateway()
    gw.router = SimpleNamespace(route_message=AsyncMock(return_value="ok"))
    gw.system_events = SimpleNamespace(enqueue=AsyncMock())
    gw.event_queue = gw.system_events
    return gw, sv


async def test_start_stop_lifecycle(gateway, monkeypatch: pytest.MonkeyPatch):
    gw, sv = gateway
    gw.running = False

    start_http = AsyncMock()
    start_heartbeat = AsyncMock()
    start_cron = AsyncMock()
    init_autonomous = AsyncMock()
    init_comms = AsyncMock()

    monkeypatch.setattr(gw, "_start_http_server", start_http)
    monkeypatch.setattr(gw, "_start_heartbeat", start_heartbeat)
    monkeypatch.setattr(gw, "_start_cron", start_cron)
    monkeypatch.setattr(gw, "_init_autonomous_modules", init_autonomous)
    monkeypatch.setattr(gw, "_init_comms", init_comms)

    watcher = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(sv, "ConfigWatcher", lambda _gw: watcher)

    class DummyTask:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def __await__(self):
            async def _done():
                return None

            return _done().__await__()

    queue_task = DummyTask()

    def _create_task(coro):
        coro.close()
        return queue_task

    async def _sleep(_seconds: float):
        raise asyncio.CancelledError()

    monkeypatch.setattr(sv.asyncio, "create_task", _create_task)
    monkeypatch.setattr(sv.asyncio, "sleep", _sleep)

    await gw.start()
    assert start_http.await_count == 1
    assert start_heartbeat.await_count == 1
    assert start_cron.await_count == 1
    assert init_autonomous.await_count == 1
    assert init_comms.await_count == 1
    assert watcher.start.await_count == 1
    assert watcher.stop.await_count == 1
    assert queue_task.cancelled is True


async def test_start_http_server_registers_routes_and_deck(
    gateway, monkeypatch: pytest.MonkeyPatch
):
    gw, sv = gateway
    gw.config_manager.global_config["telegram"] = {
        "bot_token": "token",
        "allowed_users": [1],
        "require_auth": True,
    }
    gw.config_manager.global_config["deck"] = {"enabled": True}

    register_all_routes = MagicMock()
    register_deck_routes = MagicMock()
    monkeypatch.setattr("navig.gateway.routes.register_all_routes", register_all_routes)
    monkeypatch.setattr("navig.gateway.deck.register_deck_routes", register_deck_routes)
    monkeypatch.setattr(gw, "_setup_webhook_routes", MagicMock())

    class DummyRunner:
        def __init__(self, app):
            self.app = app
            self.setup_called = False
            self.cleaned = False

        async def setup(self):
            self.setup_called = True

        async def cleanup(self):
            self.cleaned = True

    class DummySite:
        def __init__(self, runner, host, port):
            self.runner = runner
            self.host = host
            self.port = port
            self.started = False

        async def start(self):
            self.started = True

    monkeypatch.setattr(sv.web, "AppRunner", DummyRunner)
    monkeypatch.setattr(sv.web, "TCPSite", DummySite)

    await gw._start_http_server()
    assert isinstance(gw._app, web.Application)
    assert gw._app["gateway"] is gw
    register_all_routes.assert_called_once()
    register_deck_routes.assert_called_once()

    await gw._runner.cleanup()
    assert gw._runner.cleaned is True


async def test_process_queue_and_message(gateway):
    gw, _sv = gateway
    callback = MagicMock()
    await gw._process_message(
        {
            "channel": "telegram",
            "user_id": "u1",
            "message": "hello",
            "metadata": {"x": 1},
            "callback": callback,
        }
    )
    callback.assert_called_once_with("ok")

    gw.router.route_message = AsyncMock(side_effect=RuntimeError("route failure"))
    await gw._process_message({"channel": "telegram", "user_id": "u1", "message": "hello"})

    gw.running = True

    async def _capture(_msg):
        gw.running = False

    gw._process_message = _capture
    await gw._message_queue.put({"channel": "c", "user_id": "u", "message": "m"})
    await gw._process_message_queue()


async def test_cors_and_core_handlers(gateway, monkeypatch: pytest.MonkeyPatch):
    gw, sv = gateway

    deck_req = DummyRequest(path="/api/deck/status", method="OPTIONS")
    deck_resp = await gw._cors_middleware(deck_req, lambda _req: web.Response(status=204))
    assert deck_resp.status == 200
    assert deck_resp.headers["Access-Control-Allow-Origin"] == "*"

    plain_req = DummyRequest(path="/health", method="GET")

    async def _handler(_req):
        return web.Response(status=201)

    plain_resp = await gw._cors_middleware(plain_req, _handler)
    assert plain_resp.status == 201

    health = await gw._handle_health(DummyRequest())
    assert response_json(health)["status"] == "ok"

    # The `_handle_*` twins these lines drove were unregistered copies of the live
    # routes, with no auth check. Deleted; the registered handlers are covered over
    # real HTTP in test_gateway_core_routes.py, including the 401 they now assert.

    gw.running = True
    gw.start_time = datetime.now()
    gw.heartbeat_runner = SimpleNamespace(
        running=True,
        _last_heartbeat=datetime.now(),
        _next_heartbeat=datetime.now(),
    )
    gw.cron_service = SimpleNamespace(
        jobs={"j1": SimpleNamespace(enabled=True), "j2": SimpleNamespace(enabled=False)}
    )
    gw.sessions = SimpleNamespace(sessions={"s1": SimpleNamespace()})
    status = await gw._handle_status(DummyRequest())
    payload = response_json(status)
    assert payload["status"] == "running"
    assert payload["config"]["port"] == gw.config.port


async def test_message_event_session_and_ws_handlers(gateway):
    gw, sv = gateway

    bad_json = DummyRequest(json_exc=json.JSONDecodeError("bad", "x", 0))
    resp = await gw._handle_message(bad_json)
    assert resp.status == 400

    missing = DummyRequest(payload={"channel": "telegram"})
    resp = await gw._handle_message(missing)
    assert resp.status == 400

    gw.router.route_message = AsyncMock(return_value="routed")
    ok = DummyRequest(
        payload={
            "channel": "telegram",
            "user_id": "u",
            "message": "hi",
            "metadata": {"a": 1},
        }
    )
    resp = await gw._handle_message(ok)
    assert response_json(resp)["success"] is True

    evt_missing = await gw._handle_event(DummyRequest(payload={"agent_id": "x"}))
    assert evt_missing.status == 400

    hb = SimpleNamespace(request_run_now=AsyncMock())
    gw.heartbeat_runner = hb
    evt_ok = await gw._handle_event(
        DummyRequest(payload={"text": "wake", "agent_id": "n1", "wake_now": True})
    )
    assert response_json(evt_ok)["success"] is True
    assert gw.system_events.enqueue.await_count == 1
    assert hb.request_run_now.await_count == 1

    now = datetime.now(timezone.utc)
    gw.sessions = SimpleNamespace(
        sessions={
            "s1": SimpleNamespace(messages=[1, 2], created_at=now, updated_at=now),
        }
    )
    sessions = await gw._handle_list_sessions(DummyRequest())
    assert response_json(sessions)["total"] == 1

    # `_handle_ws_message` was a 5-line forwarder to routes.core._ws_dispatch, which
    # every real caller uses directly. Deleted with the other unregistered twins.


async def test_heartbeat_and_cron_handlers(gateway):
    gw, _sv = gateway

    hb_result = SimpleNamespace(
        success=True,
        suppressed=False,
        response="ok",
        issues_found=[],
        timestamp=datetime.now(timezone.utc),
    )
    gw.heartbeat_runner = SimpleNamespace(
        trigger_now=AsyncMock(return_value=hb_result),
        get_history=lambda limit=10: [{"n": limit}],
        get_status=lambda: {"running": True},
    )

    hist = await gw._handle_heartbeat_history(DummyRequest(query={"limit": "5"}))
    st = await gw._handle_heartbeat_status(DummyRequest())
    assert response_json(hist)["history"][0]["n"] == 5
    assert response_json(st)["running"] is True

    unavailable = await gw._handle_cron_list(DummyRequest())
    assert unavailable.status == 503

    class Job:
        def __init__(self, job_id="j1"):
            self.job_id = job_id
            self.enabled = True

        def to_dict(self):
            return {"id": self.job_id}

    gw.cron_service = SimpleNamespace(
        list_jobs=lambda: [Job()],
        add_job=lambda **kwargs: Job(kwargs["name"]),
        get_job=lambda job_id: Job(job_id) if job_id == "ok" else None,
        remove_job=lambda job_id: job_id == "ok",
        enable_job=lambda job_id: job_id == "ok",
        disable_job=lambda job_id: job_id == "ok",
        run_job_now=AsyncMock(return_value={"success": True, "output": "done", "error": None}),
    )

    assert response_json(await gw._handle_cron_list(DummyRequest()))["jobs"][0]["id"] == "j1"
    added = await gw._handle_cron_add(
        DummyRequest(payload={"name": "job", "schedule": "* * * * *", "command": "echo hi"})
    )
    assert response_json(added)["id"] == "job"
    assert (await gw._handle_cron_get(DummyRequest(match_info={"job_id": "missing"}))).status == 404
    assert (
        response_json(await gw._handle_cron_get(DummyRequest(match_info={"job_id": "ok"})))["id"]
        == "ok"
    )
    assert (
        await gw._handle_cron_delete(DummyRequest(match_info={"job_id": "missing"}))
    ).status == 404
    assert (
        response_json(await gw._handle_cron_delete(DummyRequest(match_info={"job_id": "ok"})))[
            "success"
        ]
        is True
    )
    assert (
        await gw._handle_cron_enable(DummyRequest(match_info={"job_id": "missing"}))
    ).status == 404
    assert (
        response_json(await gw._handle_cron_enable(DummyRequest(match_info={"job_id": "ok"})))[
            "success"
        ]
        is True
    )
    assert (
        await gw._handle_cron_disable(DummyRequest(match_info={"job_id": "missing"}))
    ).status == 404
    assert (
        response_json(await gw._handle_cron_disable(DummyRequest(match_info={"job_id": "ok"})))[
            "success"
        ]
        is True
    )
    assert (
        response_json(await gw._handle_cron_run(DummyRequest(match_info={"job_id": "ok"})))[
            "success"
        ]
        is True
    )


async def test_autonomous_init_and_comms(gateway, monkeypatch: pytest.MonkeyPatch):
    gw, sv = gateway
    gw.config_manager.global_config.update(
        {
            "browser": {"headless": False, "timeout": 12},
            "mcp": {"servers": [{"name": "local", "command": "mcp"}]},
            "webhooks": {"sources": {"github": {"secret": "sec", "provider": "github"}}},
            "comms": {"default_channel": "telegram", "matrix": {"enabled": True}},
        }
    )

    approval_mod = ModuleType("navig.approval")

    class ApprovalPolicy:
        @staticmethod
        def default():
            return "policy"

        @staticmethod
        def from_config(config):
            # The real gateway builds the policy from the operator's config
            # (`approval:` section) and falls back to defaults on empty input.
            return "policy"

    class ApprovalManager:
        def __init__(self, gateway, policy, audit_log=None):
            self.gateway = gateway
            self.policy = policy
            self.audit_log = audit_log
            self.handlers = {}

        def register_handler(self, name, handler):
            self.handlers[name] = handler

        async def request_approval(self, **kwargs):
            """The real manager's gating entry point (approval/manager.py:173).

            This fake omitted it, and `_init_autonomous_modules` binds the manager
            into the PROCESS-GLOBAL ApprovalGate — so every later test in the same
            worker that gated a dangerous tool hit
            `AttributeError: 'ApprovalManager' object has no attribute
            'request_approval'`, the backend failed closed, and an unrelated MCP test
            went red. A fake that omits a method the caller actually invokes is not a
            simplification; it is a different object wearing the name.

            The leak itself is contained by the autouse reset in tests/conftest.py —
            this keeps the fake honest so it cannot mislead through some other path.
            """
            return True

    approval_mod.ApprovalManager = ApprovalManager
    approval_mod.ApprovalPolicy = ApprovalPolicy
    monkeypatch.setitem(sys.modules, "navig.approval", approval_mod)

    approval_handlers = ModuleType("navig.approval.handlers")
    approval_handlers.GatewayApprovalHandler = lambda manager: SimpleNamespace(manager=manager)
    monkeypatch.setitem(sys.modules, "navig.approval.handlers", approval_handlers)

    browser_mod = ModuleType("navig.browser")
    browser_mod.BrowserConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    browser_mod.BrowserController = lambda cfg: SimpleNamespace(
        _browser=None, _page=None, config=cfg
    )
    monkeypatch.setitem(sys.modules, "navig.browser", browser_mod)

    mcp_mod = ModuleType("navig.mcp")

    class MCPClientManager:
        def __init__(self):
            self.clients = {}

        async def add_client(self, name, command=None, url=None):
            client = SimpleNamespace(connected=True, name=name, command=command, url=url)
            self.clients[name] = client
            return client

    mcp_mod.MCPClientManager = MCPClientManager
    monkeypatch.setitem(sys.modules, "navig.mcp", mcp_mod)

    webhooks_mod = ModuleType("navig.webhooks")

    class WebhookReceiver:
        # The server now constructs this with the global config (the real receiver reads
        # config["webhooks"] to load sources in _load_sources) — NOT with no args plus a
        # `configure_source(WebhookSourceConfig(provider=...))` loop, which called a method
        # that doesn't exist and passed a field WebhookSourceConfig doesn't have.
        def __init__(self, config=None):
            self.config = config

        def get_routes(self):
            return [("POST", "/wh", lambda _request: web.json_response({"ok": True}))]

    webhooks_mod.WebhookReceiver = WebhookReceiver
    monkeypatch.setitem(sys.modules, "navig.webhooks", webhooks_mod)

    tasks_mod = ModuleType("navig.tasks")

    class WorkerConfig:
        def __init__(self, max_concurrent):
            self.max_concurrent = max_concurrent

    class TaskQueue:
        def __init__(self, persist_path):
            self.persist_path = persist_path

    class TaskWorker:
        def __init__(self, queue, config):
            self.queue = queue
            self.config = config
            self.handlers = {}

        def handler(self, name):
            def _register(fn):
                self.handlers[name] = fn
                return fn

            return _register

        async def start(self):
            return None

    tasks_mod.WorkerConfig = WorkerConfig
    tasks_mod.TaskQueue = TaskQueue
    tasks_mod.TaskWorker = TaskWorker
    monkeypatch.setitem(sys.modules, "navig.tasks", tasks_mod)

    await gw._init_autonomous_modules()
    assert gw.approval_manager is not None
    assert gw.browser_controller is not None
    assert gw.mcp_client_manager is not None
    assert gw.webhook_receiver is not None
    # The receiver is constructed WITH the global config (so it can load the operator's
    # `webhooks:` sources), not with no args + a broken configure_source loop.
    assert gw.webhook_receiver.config is gw.config_manager.global_config
    assert gw.task_queue is not None
    assert gw.task_worker is not None

    run_result = SimpleNamespace(stdout="ok", stderr="", returncode=0)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: run_result)
    blocked = await gw.task_worker.handlers["run_command"]({"command": "echo not-allowed"})
    allowed = await gw.task_worker.handlers["run_command"](
        {"command": "navig status", "timeout": 5}
    )
    assert blocked["returncode"] == 1
    assert allowed["returncode"] == 0

    gw.send_alert = AsyncMock()
    sent = await gw.task_worker.handlers["send_alert"]({"message": "hi"})
    assert sent["sent"] is True
    assert gw.send_alert.await_count == 1

    dispatch_mod = ModuleType("navig.comms.dispatch")
    configured = {}
    dispatch_mod.configure = lambda **kwargs: configured.update(kwargs)
    monkeypatch.setitem(sys.modules, "navig.comms.dispatch", dispatch_mod)

    # The notifier comes off the LIVE channel — the only place it has ever come from.
    #
    # This used to inject a fake `navig.gateway.channels.registry` module whose
    # ChannelRegistry had an `instance()` staticmethod, and assert the notifier arrived
    # through it. The real ChannelRegistry has never had `instance()` — the accessor is
    # the module-level `get_channel_registry()` — so the production call
    # `... if hasattr(ChannelRegistry, "instance") else None` was permanently None and
    # that fallback never ran once. The fake manufactured the very method whose absence
    # was the bug, which is exactly why the absence went unnoticed: the suite was green
    # on a path that could not exist outside it.
    gw.channels["telegram"] = SimpleNamespace(_notifier="telegram-notifier")

    matrix_mod = ModuleType("navig.comms.matrix")

    class NavigMatrixBot:
        def __init__(self, cfg):
            self.cfg = cfg

        async def start(self):
            return None

    matrix_mod.NavigMatrixBot = NavigMatrixBot
    monkeypatch.setitem(sys.modules, "navig.comms.matrix", matrix_mod)

    await gw._init_comms()
    assert configured["default_channel"] == "telegram"
    assert configured["telegram_notifier"] == "telegram-notifier"

    # _setup_webhook_routes hands the receiver's RouteDefs straight to
    # router.add_routes() (since #366 — the old per-route add_post loop crashed on
    # non-iterable RouteDefs), so the mock router must expose add_routes.
    gw._app = SimpleNamespace(
        router=SimpleNamespace(add_post=MagicMock(), add_get=MagicMock(), add_routes=MagicMock())
    )
    gw._setup_webhook_routes()
    gw._app.router.add_routes.assert_called_once()


async def test_feature_handlers_and_agent_interface(
    gateway, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    gw, sv = gateway

    now = datetime.now(timezone.utc)
    req = SimpleNamespace(
        id="req1",
        action="deploy",
        level=SimpleNamespace(value="high"),
        description="desc",
        agent_id="agent",
        created_at=now,
        status=SimpleNamespace(value="pending"),
    )

    gw.approval_manager = SimpleNamespace(
        list_pending=lambda: [req],
        # The real request_approval(command=..., ...) returns a bool.
        request_approval=AsyncMock(return_value=True),
        respond=AsyncMock(return_value=True),
    )
    # The `_handle_*` twins these lines drove were unregistered copies of the live
    # routes, with no auth check. Deleted; the registered handlers are covered over
    # real HTTP in test_gateway_core_routes.py, including the 401 they now assert.
    assert (
        response_json(
            await gw._handle_approval_respond(
                DummyRequest(payload={"approved": True}, match_info={"request_id": "req1"})
            )
        )["success"]
        is True
    )

    gw.browser_controller = SimpleNamespace(
        _browser=object(),
        _page=object(),
        navigate=AsyncMock(),
        click=AsyncMock(),
        fill=AsyncMock(),
        screenshot=AsyncMock(return_value="/tmp/shot.png"),
        stop=AsyncMock(),
    )
    assert response_json(await gw._handle_browser_status(DummyRequest()))["started"] is True
    assert (
        response_json(
            await gw._handle_browser_navigate(DummyRequest(payload={"url": "https://example.com"}))
        )["success"]
        is True
    )
    assert (
        response_json(await gw._handle_browser_click(DummyRequest(payload={"selector": "#btn"})))[
            "success"
        ]
        is True
    )
    assert (
        response_json(
            await gw._handle_browser_fill(DummyRequest(payload={"selector": "#in", "value": "x"}))
        )["success"]
        is True
    )
    assert (
        response_json(
            await gw._handle_browser_screenshot(DummyRequest(payload={"full_page": True}))
        )["success"]
        is True
    )
    assert response_json(await gw._handle_browser_stop(DummyRequest()))["success"] is True

    # This fake must mirror the REAL objects: MCPClientManager exposes get_all_tools()
    # (not list_tools), MCPClient exposes the `is_connected` property (not `connected`),
    # and MCPTool carries `server_id` (not `client_name`). The old fake invented all
    # three names, which is exactly why the routes could call APIs that do not exist
    # and still pass this test — while 500ing against a real manager.
    tool = SimpleNamespace(name="tool.echo", description="Echo", server_id="local")
    gw.mcp_client_manager = SimpleNamespace(
        clients={"local": SimpleNamespace(is_connected=True)},
        get_all_tools=lambda: [tool],
        call_tool=AsyncMock(return_value={"ok": True}),
        add_client=AsyncMock(return_value=SimpleNamespace(is_connected=True)),
        remove_client=AsyncMock(),
    )
    assert (
        response_json(await gw._handle_mcp_clients(DummyRequest()))["clients"][0]["name"] == "local"
    )
    assert (
        response_json(await gw._handle_mcp_tools(DummyRequest()))["tools"][0]["name"] == "tool.echo"
    )
    assert (
        response_json(
            await gw._handle_mcp_call_tool(
                DummyRequest(
                    payload={"arguments": {"x": 1}},
                    match_info={"tool_name": "tool.echo"},
                )
            )
        )["success"]
        is True
    )
    assert (
        response_json(
            await gw._handle_mcp_connect(DummyRequest(payload={"name": "n1", "command": "run"}))
        )["connected"]
        is True
    )
    assert (
        response_json(await gw._handle_mcp_disconnect(DummyRequest(payload={"name": "n1"})))[
            "success"
        ]
        is True
    )

    tasks_mod = ModuleType("navig.tasks")

    class TaskStatus(Enum):
        queued = "queued"

    class Task:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.id = "task1"

        def to_dict(self):
            return {"id": self.id, **self.kwargs}

    tasks_mod.TaskStatus = TaskStatus
    tasks_mod.Task = Task
    monkeypatch.setitem(sys.modules, "navig.tasks", tasks_mod)

    queued_task = SimpleNamespace(to_dict=lambda: {"id": "t1"})
    gw.task_queue = SimpleNamespace(
        list_tasks=AsyncMock(return_value=[queued_task]),
        add=AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"id": "t2"})),
        get_stats=lambda: {"queued": 1},
        get=AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"id": "t1"})),
        cancel=AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"id": "t1"})),
    )
    gw.task_worker = SimpleNamespace(get_stats=lambda: {"active": 0})
    assert (
        response_json(await gw._handle_tasks_list(DummyRequest(query={"limit": "10"})))["tasks"][0][
            "id"
        ]
        == "t1"
    )
    assert (
        response_json(
            await gw._handle_tasks_add(DummyRequest(payload={"name": "x", "handler": "h"}))
        )["id"]
        == "t2"
    )
    assert response_json(await gw._handle_tasks_stats(DummyRequest()))["worker"]["active"] == 0
    assert (
        response_json(await gw._handle_tasks_get(DummyRequest(match_info={"task_id": "t1"})))["id"]
        == "t1"
    )
    assert (
        response_json(await gw._handle_tasks_cancel(DummyRequest(match_info={"task_id": "t1"})))[
            "success"
        ]
        is True
    )

    memory_mod = ModuleType("navig.memory")

    class Message:
        def __init__(self, session_key, role, content, token_count=0, metadata=None):
            self.id = "m1"
            self.session_key = session_key
            self.role = role
            self.content = content
            self.token_count = token_count
            self.metadata = metadata or {}
            self.timestamp = datetime.now(timezone.utc)

    class KnowledgeEntry:
        def __init__(self, key, content, summary=None, tags=None, source="api"):
            self.id = "k1"
            self.key = key
            self.content = content
            self.summary = summary
            self.tags = tags or []
            self.source = source
            self.created_at = datetime.now(timezone.utc)
            self.expires_at = None

        @classmethod
        def from_dict(cls, data):
            return cls(
                key=data["key"],
                content=data["content"],
                tags=data.get("tags", []),
                source=data.get("source", "api"),
            )

    memory_mod.Message = Message
    memory_mod.KnowledgeEntry = KnowledgeEntry
    monkeypatch.setitem(sys.modules, "navig.memory", memory_mod)

    session_stats = SimpleNamespace(
        session_key="s1",
        message_count=2,
        total_tokens=10,
        created_at=now,
        updated_at=now,
    )
    hist_msg = SimpleNamespace(id="m1", role="user", content="hello", timestamp=now, token_count=1)
    store = SimpleNamespace(
        list_sessions=lambda limit=50: [session_stats],
        get_history=lambda session_key, limit=100: [hist_msg],
        delete_session=lambda session_key: session_key == "s1",
        add_message=lambda message: message,
    )
    kb = SimpleNamespace(
        list_by_tag=lambda tag, limit=50: [KnowledgeEntry(key="k1", content="content", tags=[tag])],
        list_by_source=lambda source, limit=50: [
            KnowledgeEntry(key="k2", content="content", source=source)
        ],
        export_entries=lambda: [{"key": "k3", "content": "content", "tags": [], "source": "api"}],
        upsert=lambda entry, compute_embedding=False: entry,
        text_search=lambda query, limit=10, tags=None: [
            KnowledgeEntry(key="k4", content="body", tags=tags or [])
        ],
        count=lambda: 4,
    )
    monkeypatch.setattr(gw, "_get_memory_store", lambda: store)
    monkeypatch.setattr(gw, "_get_knowledge_base", lambda: kb)

    assert (
        response_json(await gw._handle_memory_sessions(DummyRequest(query={"limit": "3"})))[
            "sessions"
        ][0]["session_key"]
        == "s1"
    )
    assert (
        response_json(
            await gw._handle_memory_history(DummyRequest(match_info={"session_key": "s1"}))
        )["session_key"]
        == "s1"
    )
    assert (
        response_json(
            await gw._handle_memory_delete_session(DummyRequest(match_info={"session_key": "s1"}))
        )["success"]
        is True
    )
    assert (
        response_json(
            await gw._handle_memory_add_message(
                DummyRequest(payload={"session_key": "s1", "content": "hi"})
            )
        )["success"]
        is True
    )
    assert (
        response_json(await gw._handle_memory_knowledge_list(DummyRequest(query={"tag": "ops"})))[
            "entries"
        ][0]["key"]
        == "k1"
    )
    assert (
        response_json(
            await gw._handle_memory_knowledge_add(
                DummyRequest(payload={"key": "k5", "content": "v"})
            )
        )["success"]
        is True
    )
    assert (
        response_json(await gw._handle_memory_knowledge_search(DummyRequest(query={"q": "body"})))[
            "query"
        ]
        == "body"
    )
    assert response_json(await gw._handle_memory_stats(DummyRequest()))["knowledge"]["entries"] == 4

    # The proactive/engagement fixtures that stood here fed `_handle_proactive_*` and
    # `_handle_engagement_*` — unregistered copies of the live routes, with no auth
    # check, now deleted. The registered handlers are covered over real HTTP in
    # test_gateway_core_routes.py, including the 401 they now assert.

    session = SimpleNamespace(messages=[{"role": "user", "content": "hello"}])
    gw.sessions = SimpleNamespace(
        get_session=AsyncMock(return_value=session),
        add_message=AsyncMock(),
        save_all=AsyncMock(),
        sessions={},
    )
    gw._build_agent_context = AsyncMock(return_value={"session_messages": [], "files": {}})
    gw._call_ai = AsyncMock(return_value="AI response")
    text = await gw.run_agent_turn("agent1", "sess1", "Ping")
    assert text == "AI response"

    workspace = tmp_path / "workspace"
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    for name in [
        "AGENTS.md",
        "SOUL.md",
        "USER.md",
        "TOOLS.md",
        "HEARTBEAT.md",
        "MEMORY.md",
    ]:
        (workspace / name).write_text(f"{name} content", encoding="utf-8")
    (workspace / "memory" / f"{datetime.now().strftime('%Y-%m-%d')}.md").write_text(
        "memory today", encoding="utf-8"
    )
    gw.storage_dir = tmp_path

    ctx = await sv.NavigGateway._build_agent_context(
        gw,
        "agent1",
        SimpleNamespace(messages=[{"role": "user", "content": "x"}]),
        False,
    )
    assert "AGENTS.md" in ctx["files"]
    prompt = gw._build_system_prompt({"files": {"SOUL.md": "soul"}, "is_heartbeat": False})
    assert "Your Personality" in prompt

    ai_mod = ModuleType("navig.ai")
    ai_mod.ask_ai_with_context = lambda **kwargs: "ok"
    monkeypatch.setitem(sys.modules, "navig.ai", ai_mod)
    out = await sv.NavigGateway._call_ai(gw, {"session_messages": [], "files": {}}, "hello")
    assert out == "ok"

    ai_mod.ask_ai_with_context = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail"))
    out = await sv.NavigGateway._call_ai(gw, {"session_messages": [], "files": {}}, "hello")
    assert out.startswith("Error:")

    handler = SimpleNamespace(send=AsyncMock())
    gw.channels = {"telegram": handler}
    await gw.send_alert("alert")
    await gw.deliver_message("telegram", None, "payload")
    assert handler.send.await_count == 2

    gw._message_queue.put_nowait({"x": 1})
    assert gw.get_queue_size() == 1


def test_missions_autonomous_enabled_coerces_config_string(gateway):
    """The master kill-switch for autonomous missions must honor a config-set string. A raw
    bool() left `missions.autonomous_enabled: "false"` truthy — autonomous missions kept
    running when the operator meant to shut them off."""
    gw, _ = gateway
    cfg = gw.config_manager.global_config

    cfg["missions"] = {"autonomous_enabled": "false"}  # config-set string, NOT a bool
    assert gw._missions_autonomous_enabled() is False
    cfg["missions"] = {"autonomous_enabled": "true"}
    assert gw._missions_autonomous_enabled() is True
    cfg["missions"] = {"autonomous_enabled": True}
    assert gw._missions_autonomous_enabled() is True
    cfg.pop("missions", None)
    assert gw._missions_autonomous_enabled() is False  # default off
