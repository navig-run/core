"""No blocking call may run on the gateway's event loop. Repo-wide.

Everything under ``navig/gateway/`` runs in the daemon's single event loop. A
blocking call there does not slow one request — it freezes the WHOLE daemon:
the deck, the desktop OS, the Telegram webhook, the Lighthouse uplink, all of
it, until the call returns. This has bitten hard, repeatedly:

  * ``psutil.disk_partitions()`` in the briefing's system section — on a machine
    with a cold mapped network drive it took **81 minutes** to return, and it
    does not release the GIL, so every endpoint timed out until a restart (#137)
  * ``subprocess.run`` in the task worker's ``run_command`` handler — a whole
    CLI subprocess with a **300s** default timeout, inline (#147+)
  * the Telegram monitor cards — ``subprocess.run`` per command (30s), a
    half-second ``cpu_percent(interval=…)`` sleep, service/socket walks (#147)
  * ``socket.create_connection`` + a TLS handshake in the deck's SSL check (#139)

The pattern that is CORRECT — and that this test deliberately allows — is to
hand the blocking work to a worker::

    await asyncio.to_thread(subprocess.run, argv, timeout=30)
    await loop.run_in_executor(None, _blocking_helper, arg)

Passing a function as an ARGUMENT is a reference, not a call, so it passes. A
nested sync helper (``def _probe(): ...``) is skipped too — that is exactly what
you hand to an executor.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
GATEWAY = CORE / "gateway"
PLUGINS = CORE.parents[1] / "plugins"


def _scanned_roots() -> list[Path]:
    """Everywhere async code runs in-process with the daemon.

    The rule started at the gateway, but the loop does not stop there: the agent
    (`navig/agent/`), the comms adapters and the PLUGINS all run coroutines on
    the same event loop, and each was hiding the same class of freeze — an agent
    action shelling out for 60s, a `docker exec` with NO timeout at all, a
    plugin importer blocking 300s per link, in a loop.
    """
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    return roots


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            parts = set(f.parts)
            if "tests" in parts or "test" in parts or "scaffold-templates" in parts:
                continue
            files.append(f)
    return files

# (module, attribute) pairs that block long enough to stall the daemon. The
# module name is matched after stripping leading underscores, so `_socket.foo`
# and `import psutil as _psutil` are caught too.
BANNED: set[tuple[str, str]] = {
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_output"),
    ("subprocess", "check_call"),
    ("psutil", "disk_partitions"),
    ("psutil", "disk_usage"),
    ("psutil", "win_service_iter"),
    ("psutil", "net_connections"),
    ("socket", "create_connection"),
}


def _direct_calls(fn: ast.AST):
    """Calls in THIS function's body — not inside a nested def/lambda.

    A nested sync helper is the executor-bound pattern (``run_in_executor(None,
    _helper)``), so its contents are allowed to block; only what the coroutine
    itself executes can stall the loop.
    """

    def walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                yield child
            yield from walk(child)

    return walk(fn)


# Method names that can only ever mean the blocking psutil/socket call, whatever
# they are reached through. Matching on the RECEIVER alone missed
# `self._psutil.disk_usage(...)` — psutil held on an attribute rather than
# imported as a module — which is exactly how the agent's watch loop kept a
# blocking disk probe (and a 0.1s cpu_percent SLEEP) on the event loop.
BANNED_METHODS: set[str] = {
    "disk_partitions",
    "disk_usage",
    "win_service_iter",
    "net_connections",
    "create_connection",
    "check_output",
}


def _banned_key(call: ast.Call) -> tuple[str, str] | None:
    fn = call.func
    if not isinstance(fn, ast.Attribute):
        return None
    # `subprocess.run` etc. — generic verbs, so they must be receiver-qualified
    # (`app.run()` is not a subprocess).
    if isinstance(fn.value, ast.Name):
        key = (fn.value.id.lstrip("_"), fn.attr)
        if key in BANNED:
            return key
    if fn.attr in BANNED_METHODS:
        return ("<any>", fn.attr)
    return None


def _offenders() -> list[str]:
    out: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # a template / py2 relic — not shipped code
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for call in _direct_calls(node):
                key = _banned_key(call)
                if key:
                    out.append(f"{path.name}:{call.lineno}  {node.name}() calls {key[0]}.{key[1]}()")
    return out


def test_no_blocking_calls_on_the_event_loop():
    offenders = _offenders()
    assert not offenders, (
        "blocking call invoked directly inside an async function — everything "
        "here runs on the daemon's ONE event loop, so this freezes the WHOLE "
        "daemon (deck, OS, webhook, uplink) until it returns.\n"
        "Hand it to a worker instead:  await asyncio.to_thread(fn, ...)\n  "
        + "\n  ".join(offenders)
    )


def test_cpu_is_never_sampled_with_a_blocking_interval():
    """`psutil.cpu_percent(interval=N)` SLEEPS for N seconds. On the loop that is
    a daemon-wide stall — monitor.get_cpu_info() samples a delta instead."""
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for call in _direct_calls(node):
                fn = call.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "cpu_percent"):
                    continue
                blocking = any(
                    kw.arg == "interval"
                    and not (isinstance(kw.value, ast.Constant) and not kw.value.value)
                    for kw in call.keywords
                ) or bool(call.args)
                if blocking:
                    offenders.append(f"{path.name}:{call.lineno}  {node.name}()")
    assert not offenders, "blocking cpu_percent(interval=…) on the event loop: " + ", ".join(
        offenders
    )


def test_the_rule_actually_catches_a_regression(tmp_path):
    """Guard the guard: the AST rule must flag a direct call and must NOT flag the
    correct to_thread / nested-helper patterns (or it would be quietly useless)."""
    src = '''
import asyncio, subprocess, socket

async def bad_handler():
    subprocess.run(["ls"], timeout=30)          # <- must be caught

async def bad_via_attribute(self):
    # psutil held on an attribute, not imported as a module — the shape that
    # hid a blocking disk probe in the agent's watch loop.
    return self._psutil.disk_usage("/")

async def good_to_thread():
    await asyncio.to_thread(subprocess.run, ["ls"], timeout=30)   # reference, fine

async def good_executor():
    def _probe():
        return socket.create_connection(("h", 1), timeout=5)      # nested: fine
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _probe)

async def good_unrelated_run(app):
    return app.run()        # a generic .run() is NOT a subprocess
'''
    tree = ast.parse(src)
    flagged = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        for call in _direct_calls(node)
        if _banned_key(call)
    ]
    assert flagged == ["bad_handler", "bad_via_attribute"], f"rule is wrong — flagged {flagged}"
