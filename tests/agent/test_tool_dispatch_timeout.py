"""Tool dispatch must not block the event loop and must honour its timeout.

Two coupled defects on the agent's ReAct tool-execution path:

1. ``_run_tool_sync`` ran the tool in ``with ThreadPoolExecutor() as ex:`` — the
   ``with``-exit's ``shutdown(wait=True)`` JOINED the worker, silently defeating
   ``future.result(timeout=…)``: a hung tool wedged the caller forever.
2. ``conv/agent.py`` called ``dispatch()``/``spec.execute()`` SYNCHRONOUSLY inside
   the async ReAct loop, blocking the gateway event loop (and every concurrent
   session) for the whole tool duration — and the "parallel" batch wasn't parallel.

Fixes: ``_run_tool_sync`` shuts down without waiting on timeout; the ReAct call
site offloads via ``asyncio.to_thread`` / ``SpeculativeExecutor.aexecute`` wrapped
in ``asyncio.wait_for``. ``aexecute`` keeps cache-check + speculation ON the loop
(``_launch_speculation`` needs a running loop) while offloading only the dispatch.
"""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

# ── shared harness (mirrors the sibling dispatch tests) ──────────────────────

def _fake_llm_config():
    return SimpleNamespace(
        provider="openrouter", model="openai/gpt-4o",
        temperature=0.2, max_tokens=512, base_url=None,
    )


def _tool_call(name, args, tc_id=None):
    from navig.providers.clients import ToolCall
    tc = ToolCall.__new__(ToolCall)
    tc.id = tc_id or f"tc-{name}"
    tc.name = name
    tc.arguments = json.dumps(args)
    return tc


def _fake_usage():
    return {"prompt_tokens": 1, "completion_tokens": 1,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def _apply_common_patches(monkeypatch, registry):
    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr("navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config())
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())


# ── 1. the sync bridge no longer joins a hung worker (timeout works) ─────────

@pytest.mark.asyncio
async def test_run_tool_sync_times_out_without_joining(monkeypatch):
    from navig.agent import agent_tool_registry as atr

    monkeypatch.setattr(atr, "TOOL_TIMEOUT_SECONDS", 0.3)

    class _SlowTool:
        async def run(self, args, on_status=None):
            await asyncio.sleep(3.0)  # far longer than the patched 0.3s cap
            return SimpleNamespace(success=True, output="never")

    # Called from within a running loop → the ThreadPoolExecutor branch.
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        atr._run_tool_sync(_SlowTool(), {})
    elapsed = time.monotonic() - t0
    # Returned at ~0.3s (the cap), NOT ~3s — the old `with`-join waited for the worker.
    assert elapsed < 2.0, f"timeout did not abort promptly ({elapsed:.2f}s) — worker was joined"


# ── 2. the ReAct loop abandons a hung tool instead of wedging the turn ───────

@pytest.mark.asyncio
async def test_react_loop_abandons_hung_tool(monkeypatch):
    from navig.agent.conv import ConversationalAgent as ConvAgent
    from navig.agent.conv import agent as agent_mod

    monkeypatch.setattr(agent_mod, "_TOOL_DISPATCH_TIMEOUT", 0.3)
    # Force the non-speculative branch (asyncio.to_thread(dispatch)).
    monkeypatch.setattr("navig.agent.speculative.get_speculative_executor", lambda: None)

    # The tool hangs on an Event rather than a fixed sleep, for two measured reasons.
    # (1) Headroom: the assertion below has to sit between "abandoned" and "waited for
    # the tool", and the turn itself really costs ~1.3s (agent construction + the fake
    # round-trips, profiled). Against a 3s sleep the only honest budget was ~2s, which
    # left ~0.7s of slack — and under `-n auto` this went red at 2.30s and 2.81s on an
    # otherwise green tree. A long hang moves the ceiling far away instead.
    # (2) Teardown: an abandoned `time.sleep(3.0)` worker kept running after the test
    # returned, and draining it cost 2.68s of teardown — more than the test itself.
    # Releasing the thread once the assertion has been made removes that entirely.
    released = threading.Event()
    # Set by the worker only if it ever runs to COMPLETION. This is the real invariant --
    # "the loop returned while the tool was still hung" -- and it needs no clock.
    #
    # Wall-clock was a proxy for it and has now been re-tuned twice and still goes red on an
    # otherwise green tree: 2.30s and 2.81s against the old 2s budget (which is why the hang
    # was lengthened to 30s), then 6.37s, 6.49s and 7.76s against the 5s budget that
    # replaced it, under `-n auto` on a loaded machine. A third threshold would fail the same
    # way. `elapsed` is still measured, but only to make the failure message useful.
    completed = threading.Event()

    class _Registry:
        def get_openai_schemas(self, toolsets):
            return []

        def available_names(self, toolsets):
            return ["slow_tool"]

        def dispatch(self, name, args, vault_injector=None):
            # Hangs for the whole turn (wait_for fires at 0.3s), then exits promptly.
            released.wait(30.0)
            completed.set()
            return "should-not-be-seen"

    _apply_common_patches(monkeypatch, _Registry())

    responses = iter([
        SimpleNamespace(
            content=None,
            tool_calls=[_tool_call("slow_tool", {"x": 1}, "id-1")],
            usage=_fake_usage(),
        ),
        SimpleNamespace(content="done", tool_calls=None, usage=_fake_usage()),
    ])

    class _FakeClient:
        async def complete(self, request):
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    events = []

    # Annotate the param (non-str) so the on_status_update setter does NOT apply
    # its legacy str-callback shim — we want the StatusEvent objects themselves.
    async def sink(event: object):
        events.append(event)

    agent = ConvAgent()
    agent.on_status_update = sink

    t0 = time.monotonic()
    result = await agent.run_agentic(message="run the slow tool", max_iterations=5)
    elapsed = time.monotonic() - t0

    # Checked BEFORE releasing: once released the worker finishes and sets the flag, so the
    # order here is what makes the assertion mean anything.
    assert not completed.is_set(), (
        f"the loop waited for the hung tool ({elapsed:.2f}s) — it ran to completion instead "
        "of being abandoned"
    )

    released.set()  # let the abandoned worker exit instead of draining it in teardown

    assert result == "done"
    # The sink also receives streamed content strings; keep only StatusEvent objects.
    failed = [
        e for e in events
        if getattr(e, "type", None) == "step_failed"
        and getattr(e, "metadata", {}).get("tool") == "slow_tool"
    ]
    assert failed, "the timed-out tool did not emit step_failed"


# ── 3. aexecute offloads the blocking dispatch off the loop ──────────────────

@pytest.mark.asyncio
async def test_aexecute_offloads_dispatch_to_a_worker_thread():
    from navig.agent.speculative import SpeculativeExecutor

    loop_thread = threading.get_ident()
    seen = {}

    def _dispatch(tool, args):
        seen["thread"] = threading.get_ident()
        return f"result-{tool}"

    ex = SpeculativeExecutor(_dispatch, config={"enabled": False})
    # write_file is NOT read-only → always dispatches (no cache short-circuit).
    result = await ex.aexecute("write_file", {"path": "x"})

    assert result == "result-write_file"
    assert seen["thread"] != loop_thread, "dispatch ran on the loop thread — not offloaded"


# ── 4. aexecute serves a speculative cache hit without dispatching ───────────

@pytest.mark.asyncio
async def test_aexecute_serves_cache_hit_without_dispatch():
    from navig.agent.speculative import SpeculativeExecutor

    calls = []

    def _dispatch(tool, args):
        calls.append((tool, args))
        return f"result-{tool}"

    ex = SpeculativeExecutor(_dispatch, config={"enabled": False})
    # Pre-populate the speculative cache the way _speculative_run would.
    ex.cache.put("read_file", {"path": "a"}, "cached-value")

    result = await ex.aexecute("read_file", {"path": "a"})

    assert result == "cached-value", "aexecute did not serve the speculative cache hit"
    assert calls == [], "aexecute dispatched despite a cache hit"
