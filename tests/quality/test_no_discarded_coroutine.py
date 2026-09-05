"""No call may create a coroutine and throw it away — the operation never runs.

Calling an ``async def`` without ``await`` does not start it. It builds a coroutine
object, the statement discards it, and execution continues as if the work had been
done. Python's only complaint is a ``RuntimeWarning: coroutine '...' was never
awaited`` emitted **at garbage-collection time**, on whatever thread the collector
happens to run — which in a daemon means it lands in no test, no log the operator
reads, and no traceback pointing at the call site.

That makes it a member of this repo's most expensive family: a phantom success. The
call site reads ``self._flush_batch()`` and every line after it assumes the flush
happened. Sibling guards already cover orphaned tasks, defeated timeouts and
shell-spawn orphans; this is the one shape none of them can see, because there is no
task, no timeout and no subprocess — just a statement that did nothing.

**Resolution is precise, not name-based, and that distinction is measured.** A first
draft matched any call whose name was defined ``async def`` anywhere in the tree and
reported **187** sites, of which ~150 were stdlib collisions — ``time.sleep``,
``shutil.move``, ``proc.kill``, ``Path.rename`` — because some navig class happens to
define an async method by that name. Name matching cannot tell those apart. So only
two forms are considered, each of which resolves the callee without inference:

* ``self.method()`` where ``method`` is defined ``async def`` on **that same class**;
* a bare ``name()`` where ``name`` is a module-level ``async def`` in **that same file**.

Anything else — an imported symbol, an attribute on another object — is out of scope
rather than guessed at. Under those two rules the tree reports **8** sites, and all
eight are Textual TUI screens whose methods carry ``@work``.

**The ``@work`` exemption is semantic, not an allowlist.** ``textual.work`` converts an
async method into one that schedules a Worker and returns it, so a bare call is the
documented, correct usage::

    def on_mount(self) -> None:
        self._run_boot_sequence()      # correct: @work(exclusive=True) below

The guard therefore skips any async def carrying a decorator named in
``SCHEDULING_DECORATORS``. **If you introduce another decorator with those semantics,
add it there** — an unlisted one makes this guard report a false positive, which is the
safe direction; the reverse (silently treating a real coroutine as scheduled) is not.

Baseline: **zero** real sites. The fix when this trips is almost always to ``await``
the call, or — if the caller is synchronous and cannot — to hand it to
``navig.core.background.spawn()``, which is this repo's GC-safe fire-and-forget helper.
"""
from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
PLUGINS = Path(__file__).resolve().parents[3] / "plugins"

# Decorators that turn an `async def` into a call that SCHEDULES the coroutine and
# returns a handle. A bare call to one of these is correct usage, not a discard.
SCHEDULING_DECORATORS = {"work"}

# Matched against the path RELATIVE to each root, never the absolute path. An agent
# session runs from a git worktree under `.dev/worktrees/<slug>/`, so an absolute-path
# match on `/.dev/` skips the entire tree and the guard passes having read NOTHING.
SKIP_PARTS = ("/tests/", "/.lab/", "/build/", "/node_modules/")


def _decorator_names(fn: ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for dec in fn.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        name = getattr(node, "attr", None) or getattr(node, "id", None)
        if name:
            names.add(name)
    return names


def _bare_call_statements(fn: ast.AST) -> list[ast.Expr]:
    """Bare `foo(...)` statements inside fn, NOT descending into nested defs.

    A nested def has its own contract — its body runs when IT is called, so a
    statement inside it must be judged against that function, not this one.
    """
    found: list[ast.Expr] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                found.append(child)
            walk(child)

    walk(fn)
    return found


def find_discards(source: str, label: str) -> list[str]:
    """Both rules, over one file's source. Split out so the anti-vacuity test can
    drive it with a synthetic module instead of a temp file on disk."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    out: list[str] = []

    def report(node: ast.Expr, why: str) -> None:
        text = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        out.append(f"{label}:{node.lineno}: {text[:90]}  <- {why}")

    # RULE 1 — self.method() where method is async on this very class.
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        async_methods = {
            n.name
            for n in cls.body
            if isinstance(n, ast.AsyncFunctionDef)
            and not (_decorator_names(n) & SCHEDULING_DECORATORS)
        }
        # A sync def of the same name (an overload, a subclass shim) makes it ambiguous.
        async_methods -= {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        if not async_methods:
            continue
        for method in cls.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for expr in _bare_call_statements(method):
                func = expr.value.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"
                    and func.attr in async_methods
                ):
                    report(expr, f"self.{func.attr}() is `async def` on {cls.name}")

    # RULE 2 — bare name() where name is a module-level async def in this file.
    module_async = {
        n.name
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef)
        and not (_decorator_names(n) & SCHEDULING_DECORATORS)
    }
    module_async -= {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if module_async:
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for expr in _bare_call_statements(fn):
                func = expr.value.func
                if isinstance(func, ast.Name) and func.id in module_async:
                    report(expr, f"{func.id}() is an `async def` in this module")
    return out


def _scan_tree() -> tuple[list[str], int]:
    offenders: list[str] = []
    scanned = 0
    for root in (CORE, PLUGINS):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = "/" + path.relative_to(root).as_posix()
            if any(part in rel for part in SKIP_PARTS):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            if "async def" not in source:
                continue
            offenders.extend(find_discards(source, path.name))
    return offenders, scanned


def test_no_coroutine_is_created_and_discarded() -> None:
    offenders, _ = _scan_tree()

    assert not offenders, (
        "these statements call an `async def` without awaiting it, so the coroutine is "
        "built and thrown away and THE OPERATION NEVER RUNS. Python reports this only as "
        "a RuntimeWarning at GC time, which in the daemon reaches nobody — the call site "
        "reads exactly like a completed operation.\n"
        "Fix: `await` it. If the caller is synchronous and cannot await, hand it to "
        "`navig.core.background.spawn()` (the GC-safe fire-and-forget helper) so the task "
        "is owned rather than dropped.\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_detector_actually_finds_a_planted_discard() -> None:
    """Anti-vacuity. The tree is at zero, so a broken detector and a clean tree look
    identical from the outside. Both rules are driven against a synthetic module."""
    planted = find_discards(
        "class Notifier:\n"
        "    async def flush(self) -> None: ...\n"
        "    def on_event(self) -> None:\n"
        "        self.flush()\n",
        "planted.py",
    )
    assert len(planted) == 1 and "self.flush()" in planted[0], (
        f"RULE 1 (self.<async method>()) stopped detecting anything: {planted}"
    )

    planted = find_discards(
        "async def deliver() -> None: ...\n"
        "def send() -> None:\n"
        "    deliver()\n",
        "planted.py",
    )
    assert len(planted) == 1 and "deliver()" in planted[0], (
        f"RULE 2 (module-level async def) stopped detecting anything: {planted}"
    )


def test_the_detector_does_not_flag_correct_code() -> None:
    """The exemptions are load-bearing: without them this guard is unusable noise.

    `@work` alone accounts for all 8 sites the raw rules report tree-wide.
    """
    awaited = find_discards(
        "class C:\n"
        "    async def flush(self) -> None: ...\n"
        "    async def go(self) -> None:\n"
        "        await self.flush()\n",
        "ok.py",
    )
    assert not awaited, f"an awaited call must not be flagged: {awaited}"

    scheduled = find_discards(
        "from textual import work\n"
        "class Screen:\n"
        "    @work(exclusive=True)\n"
        "    async def boot(self) -> None: ...\n"
        "    def on_mount(self) -> None:\n"
        "        self.boot()\n",
        "ok.py",
    )
    assert not scheduled, f"@work SCHEDULES the coroutine — not a discard: {scheduled}"

    handed_off = find_discards(
        "async def deliver() -> None: ...\n"
        "def send() -> None:\n"
        "    spawn(deliver())\n"
        "    asyncio.create_task(deliver())\n"
        "    return deliver()\n",
        "ok.py",
    )
    assert not handed_off, (
        f"a coroutine passed to a consumer or returned is not discarded: {handed_off}"
    )

    nested = find_discards(
        "class C:\n"
        "    async def flush(self) -> None: ...\n"
        "    def outer(self) -> None:\n"
        "        async def inner() -> None:\n"
        "            await self.flush()\n",
        "ok.py",
    )
    assert not nested, f"a nested def has its own contract: {nested}"


def test_the_scan_reaches_the_tree_it_claims_to_cover() -> None:
    """A guard that silently reads nothing is worse than no guard — it reports green
    over whatever it failed to open (this repo shipped exactly that: a dead-path guard
    that read 1898 of 6429 files and called the repo clean)."""
    _, scanned = _scan_tree()
    assert scanned > 800, (
        f"only {scanned} source files were scanned; core/navig + plugins is far larger, "
        "so the roots or the skip list are wrong"
    )
    assert CORE.is_dir(), f"core root missing: {CORE}"
    assert PLUGINS.is_dir(), f"plugins root missing: {PLUGINS}"
