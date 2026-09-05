"""No orphaned fire-and-forget task. Repo-wide (core/navig + first-party plugins).

``asyncio`` keeps only a **weak** reference to the result of ``create_task()`` /
``ensure_future()``. A background task whose handle the caller discards can be
garbage-collected *before it runs* — silently losing whatever it was going to do
(finish the bot's setup, forward an event, register a rotated URL, send a
keepalive, run a retry). In the long-lived daemon that is a whole class of
"it just never happened" bugs, and it is invisible: nothing errors, every light
stays green.

The canonical fix is :func:`navig.core.background.spawn`, which holds a strong
reference until the task finishes (then releases it) and logs any escaping
exception. A whole sweep converted the tree to it — the reachability cluster
(#619), the remaining short-lived + loop sites (#621), the ``loop.create_task``
variants (#624), and navig-audio's wake-word callback (this PR). This guard keeps
it converted: it fails the build if a NEW bare ``create_task``/``ensure_future`` is
scheduled and its handle thrown away.

What is a violation — precisely the GC-drop footgun and nothing else::

    asyncio.create_task(coro())            # <- discarded → collectable
    asyncio.ensure_future(coro())          # <- discarded
    loop.create_task(coro())               # <- discarded (loop-held tasks are weak too)
    asyncio.get_running_loop().create_task(coro())

What is CORRECT — and deliberately allowed::

    spawn(coro())                          # the canonical helper — holds a ref
    task = asyncio.create_task(coro())     # assigned → caller holds it (cancel later)
    self._task = asyncio.create_task(...)  # stored on the instance
    return asyncio.create_task(coro())     # returned → caller owns it
    await asyncio.create_task(coro())      # awaited → runs to completion here
    async with asyncio.TaskGroup() as tg:
        tg.create_task(coro())             # the group holds refs + awaits them

Only a call on the ``asyncio`` module OR an event loop is flagged; a
``TaskGroup``/nursery ``create_task`` is safe by design and never matched.

Scope: ``core/navig/`` plus every first-party ``plugins/navig-*`` (they depend on
core, so ``spawn`` is importable there too). A standalone plugin with no core
dependency would instead hold the handle on ``self`` — that path is allowed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]
PLUGINS = REPO / "plugins"

# Calls that yield an event loop — ``asyncio.get_running_loop().create_task(...)``.
_LOOP_FACTORIES = {"get_event_loop", "get_running_loop", "new_event_loop"}
# The scheduling methods whose weakly-held result is the footgun.
_TASK_METHODS = {"create_task", "ensure_future"}


def _receiver_is_loop_like(recv: ast.expr) -> bool:
    """True when *recv* is the ``asyncio`` module or an event loop.

    This is what makes the rule precise: a ``TaskGroup``/nursery holds strong
    references to its children and awaits them, so ``tg.create_task(...)`` is
    safe and must NOT be flagged. Only the ``asyncio`` module and event loops
    hand back a weakly-held task.
    """
    if isinstance(recv, ast.Name):
        return recv.id == "asyncio" or "loop" in recv.id.lower()
    if isinstance(recv, ast.Attribute):  # e.g. ``self._loop.create_task``
        return "loop" in recv.attr.lower()
    if isinstance(recv, ast.Call):  # e.g. ``asyncio.get_running_loop().create_task``
        f = recv.func
        return isinstance(f, ast.Attribute) and f.attr in _LOOP_FACTORIES
    return False


def _is_orphan_task(node: ast.AST) -> bool:
    """A bare expression statement that schedules a task and discards the handle."""
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return False
    fn = node.value.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr in _TASK_METHODS
        and _receiver_is_loop_like(fn.value)
    )


def _tasky_call(value: ast.expr) -> ast.Call | None:
    """The ``<loop-like>.create_task(...)`` call in *value*, if that's what it is."""
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)):
        return None
    if value.func.attr not in _TASK_METHODS:
        return None
    return value if _receiver_is_loop_like(value.func.value) else None


def _self_attr(node: ast.expr) -> str | None:
    """``self._pending`` -> ``"_pending"``; anything else -> None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _self_coro_method(call: ast.Call) -> str | None:
    """``create_task(self._confirm())`` -> ``"_confirm"``."""
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Call):
        return _self_attr(arg.func)
    return None


def _clears_self_attr(fn: ast.AST, attr: str) -> int | None:
    """Line where *fn* assigns ``self.<attr> = None``, if it does."""
    for sub in ast.walk(fn):
        if (
            isinstance(sub, ast.Assign)
            and len(sub.targets) == 1
            and _self_attr(sub.targets[0]) == attr
            and isinstance(sub.value, ast.Constant)
            and sub.value.value is None
        ):
            return sub.lineno
    return None


def _self_nulling_handles(tree: ast.AST) -> list[tuple[int, str, int]]:
    """``self.X = create_task(self.Y())`` where ``Y`` itself sets ``self.X = None``.

    The rule above treats *assigned* as safe, and it is — but only while the
    assignment persists. When the scheduled coroutine clears the very attribute
    holding it, the running task becomes unreferenced **mid-flight** and asyncio's
    weak reference is all that is left. That is the original footgun with an extra
    step, and a call-site-only check cannot see it: the discard happens in the
    callee.

    Nulling the handle is often deliberate (it re-arms a guard, or stops a later
    event from cancelling work that is now committed). The fix is not to keep the
    attribute set — it is to let ``spawn()`` own the reference, so the attribute is
    free to mean whatever the state machine needs.
    """
    methods = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: list[tuple[int, str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        attr = _self_attr(node.targets[0])
        if attr is None:
            continue
        call = _tasky_call(node.value)
        if call is None:
            continue
        coro = _self_coro_method(call)
        if coro is None or coro not in methods:
            continue
        cleared_at = _clears_self_attr(methods[coro], attr)
        if cleared_at is not None:
            out.append((node.lineno, f"self.{attr} (cleared in {coro}", cleared_at))
    return out


def _scanned_roots() -> list[Path]:
    """core/navig plus every first-party plugin.

    A dropped task in a plugin runs on the same daemon event loop and vanishes
    just as silently — navig-audio's wake-word engine fired the detection callback
    with a bare ``asyncio.create_task``. First-party plugins depend on core, so
    ``spawn`` is importable there too. Same growth path as ``test_no_loop_blocking``,
    which started at the gateway and expanded to the agent + plugins.
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


def _offenders() -> list[str]:
    out: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # a template / relic — not shipped code
            continue
        for node in ast.walk(tree):
            if _is_orphan_task(node):
                out.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return out


def _self_nulling_offenders() -> list[str]:
    out: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for lineno, what, cleared_at in _self_nulling_handles(tree):
            out.append(f"{path.relative_to(REPO)}:{lineno} — {what} at line {cleared_at})")
    return out


def test_no_orphan_fire_and_forget_tasks():
    offenders = _offenders()
    assert not offenders, (
        "a task is scheduled and its handle discarded — asyncio holds only a WEAK "
        "reference, so it can be garbage-collected before it runs, silently losing "
        "the work (with every light green).\n"
        "Use navig.core.background.spawn(coro) instead — it holds a strong ref and "
        "logs exceptions. For a task you must CANCEL later, store the handle on self.\n  "
        + "\n  ".join(offenders)
    )


def test_the_rule_catches_a_regression():
    """Guard the guard: the rule must flag every discarded form and NOT flag the
    correct ones (assigned / returned / awaited / spawn / TaskGroup), or it would
    be quietly useless."""
    src = '''
import asyncio
from navig.core.background import spawn

async def bad_asyncio(): asyncio.create_task(foo())              # flag
async def bad_ensure(): asyncio.ensure_future(foo())            # flag
async def bad_loop(loop): loop.create_task(foo())               # flag
async def bad_self_loop(self): self._loop.create_task(foo())    # flag
async def bad_factory(): asyncio.get_running_loop().create_task(foo())  # flag

async def good_spawn(): spawn(foo())                            # ok
async def good_assign(): t = asyncio.create_task(foo())         # ok
async def good_store(self): self._task = asyncio.create_task(foo())  # ok
async def good_return(): return asyncio.create_task(foo())      # ok
async def good_await(): await asyncio.create_task(foo())        # ok
async def good_taskgroup():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(foo())                                   # ok (group holds refs)
async def good_arg(bucket): bucket.append(asyncio.create_task(foo()))  # ok
'''
    tree = ast.parse(src)
    flagged = sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        for stmt in ast.walk(node)
        if _is_orphan_task(stmt)
    )
    assert flagged == [
        "bad_asyncio",
        "bad_ensure",
        "bad_factory",
        "bad_loop",
        "bad_self_loop",
    ], f"rule is wrong — flagged {flagged}"


def test_no_task_handle_cleared_by_its_own_coroutine():
    """``self.X = create_task(self.Y())`` where ``Y`` sets ``self.X = None``.

    The rule above accepts *assigned* as safe — correctly, but only for as long as
    the assignment lasts. A coroutine that clears the attribute holding its own task
    leaves it unreferenced mid-flight, which is the original footgun one step
    removed. ``notify/producers/connectivity.py`` did exactly this: it dropped
    ``_pending`` before dispatching (so a late "online" could not cancel a send that
    was already committed) and then awaited a network fan-out with nothing holding
    the task. Its own package docstring names "a connectivity flap" as the very
    thing ``spawn()`` exists to protect.
    """
    offenders = _self_nulling_offenders()
    assert not offenders, (
        "a task is stored on self and then its OWN coroutine clears that attribute, "
        "so the running task loses its last strong reference — asyncio keeps only a "
        "weak one and it can be collected mid-flight.\n"
        "Clearing the handle is often deliberate (re-arming a guard, or stopping a "
        "later event from cancelling committed work). Don't fight that — schedule via "
        "navig.core.background.spawn(coro) (or notify.producers.spawn) so the "
        "reference is owned independently of the attribute.\n  " + "\n  ".join(offenders)
    )


def test_the_self_nulling_rule_catches_a_regression():
    """Guard the guard: it must flag the real shape and none of the safe ones."""
    src = '''
import asyncio
from navig.notify.producers import spawn

class Bad:
    def arm(self):
        self._pending = asyncio.create_task(self._confirm())     # flag
    async def _confirm(self):
        await asyncio.sleep(1)
        self._pending = None                                     # <- drops own ref
        await self._send()

class BadLoop:
    def arm(self):
        self._t = self._loop.create_task(self._work())           # flag
    async def _work(self):
        self._t = None
        await asyncio.sleep(1)

class GoodSpawn:
    def arm(self):
        self._pending = spawn(self._confirm())                   # ok — spawn owns it
    async def _confirm(self):
        self._pending = None
        await asyncio.sleep(1)

class GoodKeepsHandle:
    def arm(self):
        self._task = asyncio.create_task(self._work())           # ok — never cleared
    async def _work(self):
        await asyncio.sleep(1)

class GoodClearsSomethingElse:
    def arm(self):
        self._task = asyncio.create_task(self._work())           # ok
    async def _work(self):
        self._cache = None
        await asyncio.sleep(1)

class GoodClearedByCaller:
    def arm(self):
        self._task = asyncio.create_task(self._work())           # ok — caller clears it
    async def _work(self):
        await asyncio.sleep(1)
    def disarm(self):
        self._task = None
'''
    tree = ast.parse(src)
    flagged = sorted(
        cls.name
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
        if _self_nulling_handles(cls)
    )
    assert flagged == ["Bad", "BadLoop"], f"rule is wrong — flagged {flagged}"
