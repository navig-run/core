"""Every MCP tool must be classified, because an unclassified one is silently ungated.

``mcp_server._gate_tool`` reads the level like this::

    safety = getattr(self, "_tool_safety", {}).get(tool_name, "safe")
    if safety == "safe":
        return

So a tool nobody classified is not merely unprotected — it is also **unaudited**, because
the ``[mcp.gate]`` log line lives after that early return. The gate itself is careful: it
fails CLOSED when the approval backend cannot load, on the stated grounds that
``cdp_eval`` "can read a password field". But it only ever protected the tools someone
remembered to name, and for a long time that was one module out of thirteen.

What was running ungated and unaudited:

* ``desktop_powershell`` — "Execute a PowerShell command or script on the local machine";
* ``navig_run_command`` — any navig verb, including the destructive ones;
* ``desktop_process_kill``, ``desktop_registry_set``, ``desktop_registry_delete``;
* ``desktop_ahk`` (arbitrary AutoHotkey), ``desktop_type`` (types into whatever has
  focus), ``desktop_shortcut`` (key combos reach the OS);
* ``desktop_filesystem`` — one tool whose eight modes include delete;
* every ``connector_*_act`` — reply/create/update/delete/archive/label/send/move against
  Gmail, Slack, GitHub, Notion, Linear, Supabase, Google Calendar. "Send mail as the
  operator" was a safe-by-default tool call.

The rule is now: **no tool ships without a level.** Read-only tools are listed as "safe"
explicitly rather than falling through, so the registry distinguishes "considered, and it
is safe" from "nobody looked". That distinction is the whole point — it is the same
lesson as `navig doctor`'s ✓, where a green tick for an unknown was worse than a red one.

Connector tools are generated per installed connector, so they cannot be listed
statically; ``connectors.register`` derives the level from the capability suffix, and an
unrecognised suffix becomes "dangerous" rather than defaulting to safe.
"""

from __future__ import annotations

import functools
from unittest.mock import MagicMock

import pytest

VALID_LEVELS = {"safe", "moderate", "dangerous"}


@functools.lru_cache(maxsize=1)
def _registered() -> tuple[dict, dict]:
    """(handlers, safety) after a full tool registration on a bare server double.

    Cached so the 17 checks below share one registration rather than repeating it.
    Registration is cheap (~0.3s), so this is tidiness, not a fix for a real cost.
    Returns tuples because ``lru_cache`` needs a hashable result.
    """
    from navig.mcp.tools import register_all_tools

    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}
    del server._tool_safety  # force register_all_tools to create it, as the real one does
    register_all_tools(server)
    return (
        tuple(sorted(server._tool_handlers)),
        tuple(sorted(server._tool_safety.items())),
    )


def test_every_registered_tool_has_an_explicit_level() -> None:
    handlers, safety = _registered()
    missing = sorted(set(handlers) - {n for n, _ in safety})

    assert not missing, (
        "These MCP tools have no entry in `_tool_safety`, so `_gate_tool` defaults them "
        "to \"safe\" and they run with no approval check AND no audit line. Classify each "
        "one where its module registers its handlers (safe / moderate / dangerous):\n"
        + "\n".join(f"  {m}" for m in missing)
    )


def test_no_stale_classification_entries() -> None:
    """A level for a tool that does not exist is a rubber stamp — it protects nothing.

    Connector tools are generated per installed connector, so a level for a connector
    that is not registered here is expected; anything else is a typo or a leftover.
    """
    handlers, safety = _registered()
    stale = sorted(
        n for n, _ in safety if n not in set(handlers) and not n.startswith("connector_")
    )

    assert not stale, (
        "These names are classified but are not registered tools — most likely a typo or "
        "a key copied from a result payload. Delete them:\n" + "\n".join(f"  {s}" for s in stale)
    )


def test_levels_are_from_the_known_set() -> None:
    _handlers, safety = _registered()
    bad = {n: lvl for n, lvl in safety if lvl not in VALID_LEVELS}

    assert not bad, (
        f"`_gate_tool` only distinguishes {sorted(VALID_LEVELS)} — any other string is "
        f"treated as not-safe by accident rather than by intent:\n  {bad}"
    )


@pytest.mark.parametrize(
    "tool",
    [
        "desktop_powershell",       # arbitrary local code execution
        "navig_run_command",        # any navig verb
        "desktop_process_kill",
        "desktop_registry_set",
        "desktop_registry_delete",
        "desktop_ahk",              # arbitrary AutoHotkey
        "desktop_type",             # types into whatever has focus
        "desktop_shortcut",
        "desktop_filesystem",       # includes a delete mode
        "navig_block_apply",        # runs a block's steps
        "navig_agent_service_install",
    ],
)
def test_known_dangerous_tools_are_gated(tool: str) -> None:
    """Name the ones that matter, so a future refactor cannot quietly downgrade them."""
    _handlers, safety = _registered()
    levels = dict(safety)
    if tool not in levels:
        pytest.skip(f"{tool} is not registered on this platform")
    assert levels[tool] == "dangerous", f"{tool} must stay 'dangerous', got {levels[tool]!r}"


def test_connector_write_tools_are_dangerous() -> None:
    """`connector_*_act` is reply/create/update/delete/archive/label/send/move."""
    _handlers, safety = _registered()
    act = {n: lvl for n, lvl in safety if n.startswith("connector_") and n.endswith("_act")}

    assert act, "no connector _act tools were registered — the check would be vacuous"
    wrong = {k: v for k, v in act.items() if v != "dangerous"}
    assert not wrong, f"connector write tools must be 'dangerous': {wrong}"


def test_connector_reads_are_not_safe() -> None:
    """They read a mailbox, a drive, a bank account — an operator may want to know."""
    _handlers, safety = _registered()
    reads = {
        n: lvl
        for n, lvl in safety
        if n.startswith("connector_") and n.endswith(("_search", "_fetch"))
    }

    assert reads, "no connector read tools were registered — the check would be vacuous"
    wrong = {k: v for k, v in reads.items() if v == "safe"}
    assert not wrong, f"connector reads touch private third-party data: {wrong}"


def test_the_gate_still_treats_unlisted_as_safe() -> None:
    """Pin the premise. If this ever changes, the coverage rule above can relax.

    The default is deliberately not flipped to fail-closed here: that would gate every
    read-only inventory call too, and the honest fix is to classify tools rather than to
    make the gate guess.
    """
    import inspect

    from navig.mcp_server import MCPProtocolHandler

    src = inspect.getsource(MCPProtocolHandler._gate_tool)
    assert '.get(tool_name, "safe")' in src
    assert 'if safety == "safe":' in src


def test_each_bundle_register_is_self_sufficient() -> None:
    """`module.register(server)` must work on a server that has no `_tool_safety` yet.

    `register_all_tools` creates the dict before delegating, so the aggregate path always
    worked. But a bundle called DIRECTLY — by a test, or by a plugin host that builds its
    own server — got `AttributeError: 'SimpleNamespace' object has no attribute
    '_tool_safety'`. `cdp.py` had always guarded with `if not hasattr(...)`; the twelve
    bundles that gained a classification did not, and that broke seven existing tests.

    Each bundle owns its own preconditions, so this drives every one of them on a bare
    object rather than trusting the aggregator to have prepared the ground.
    """
    import importlib
    import pkgutil
    import sys
    from types import SimpleNamespace

    import navig.mcp.tools as pkg

    failures: list[str] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"navig.mcp.tools.{info.name}")
        register = getattr(module, "register", None)
        if register is None:
            continue
        if info.name == "windows" and sys.platform != "win32":
            continue
        # deliberately bare: only what the documented contract provides
        server = SimpleNamespace(tools={}, _tool_handlers={})
        try:
            register(server)
        except Exception as exc:  # noqa: BLE001 — the point is that nothing raises
            failures.append(f"{info.name}.register(): {type(exc).__name__}: {exc}")

    assert not failures, (
        "These bundles cannot be registered on their own. Guard the attribute before "
        "using it (`if not hasattr(server, \"_tool_safety\"): server._tool_safety = {}`):\n"
        + "\n".join(f"  {f}" for f in failures)
    )
