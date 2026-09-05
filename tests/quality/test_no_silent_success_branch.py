"""A failed operation must never be reported by SILENCE.

    if manager.remove_trigger(tid):
        ch.success("Removed")      # <- and no else

When that call returns False the command prints NOTHING AT ALL and exits 0. That is
worse than the error-then-return shape `test_command_exit_honesty.py` catches: there
is no ✗ to notice, so the command simply looks like it did the job. And no
error-then-return scan can see it — nothing in the source pairs an error call with a
return.

It has happened twice. `navig trigger add|remove|enable|disable` (#752, four commands)
and `navig links delete` (#753), where the same function already exited 1 on not-found
and aborted on a declined confirm — it was inconsistent with itself, one line apart.

WHY THIS ONE IS A WHOLE-TREE BAN and the sibling discard-shape is not
---------------------------------------------------------------------
A guard earns a hard zero only when it is precise enough not to cry wolf. Measured on
the tree at the time of writing:

  * this shape, narrowly defined: **1 site** in core, **0** in plugins — fixed, so the
    baseline is a true zero.
  * the related "caller discards a failure bool" shape: **14 sites vs 41 correct
    consumptions**. A blocking guard there would be noise, so none was shipped — it
    is recorded as a known, measured, unguarded class instead. That decision still
    stands (re-measured: 18 same-class sites, still far too many benign to ban).

    But do NOT inherit the reason it was written with. This note used to name
    "notification fan-out" and "`stop()` during shutdown" as the examples of
    deliberate best-effort. Both were audited and both were BUGS:
    `TelegramNotifier._process_queue` discarded `_send_notification`'s result and
    removed the row regardless, so one Telegram 429 dropped a HIGH alert for good;
    and `stop()` never cancelled the batch-flush task at all, so a pending flush
    outlived shutdown. `MatrixNotifier._flush_batch` — the other ChannelNotifier —
    had the same drain-before-send drop, which is what "fan-out is best-effort"
    reads like from the outside.

    "The caller ignores the bool because it does not care" is a CLAIM about intent.
    The only way to know is to read what the caller does NEXT: if it then clears a
    buffer, removes a queue row, or stamps `last_run`, that is not best-effort — it
    is data loss with a log line as the receipt. Unguarded means "triage each one",
    not "assume each one is fine".

The narrowness is doing real work. A broader variant of this same check (success call
ANYWHERE in the body, rather than a body made only of report calls) returns 17 hits,
and every one sampled was legitimate: `if php_info.get("config_path")` (a lookup),
`if ch.confirm_action(...)` (a user DECLINING is not a failure), `if
source.startswith("pip:")` (a branch). So:

  * the test must BE a call — `if some_flag:` is ordinary conditional reporting;
  * `if not f(...)` is skipped — that shape already handles its failure branch;
  * the body must be report calls ONLY — anything else means the `if` is doing work,
    not just announcing, and skipping that work may be entirely intended;
  * value-returning functions are skipped — they signal to a caller, a contract this
    guard has no business judging (same rule as the exit-honesty guard).
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
PLUGINS = CORE.parents[1] / "plugins"

# Console calls that only REPORT. A body made solely of these is announcing, not doing.
_REPORT_ONLY = frozenset({"success", "info", "dim"})


def _mutating_call(test: ast.expr) -> str | None:
    """The test is a direct call — `f(...)` or `obj.method(...)`.

    `if not f(...)` returns None on purpose: that shape already gives the failure its
    own branch, which is the fix, not the bug.
    """
    if isinstance(test, ast.Call):
        fn = test.func
        if isinstance(fn, ast.Name):
            return fn.id
        if isinstance(fn, ast.Attribute):
            return fn.attr
    return None


def _announces_success_only(body: list[ast.stmt]) -> bool:
    """Every statement is a report call and at least one is `success`."""
    if not body:
        return False
    saw_success = False
    for stmt in body:
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            return False
        fn = stmt.value.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)):
            return False
        if fn.attr not in _REPORT_ONLY:
            return False
        saw_success = saw_success or fn.attr == "success"
    return saw_success


def _returns_a_value(fn: ast.AST) -> bool:
    """Skip helpers: they report failure to a CALLER by value, not by exit code.

    Does not descend into nested defs — an inner `return x` must not excuse its
    enclosing command (`ast.walk` cannot express that, so walk children explicitly).
    """
    def scan(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return) and child.value is not None:
                if not (
                    isinstance(child.value, ast.Constant) and child.value.value is None
                ):
                    return True
            if scan(child):
                return True
        return False

    return scan(fn)


def _python_files() -> list[Path]:
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    out: list[Path] = []
    for root in roots:
        for f in root.rglob("*.py"):
            if {"build", "dist", "tests", "test", "scaffold-templates"} & set(f.parts):
                continue
            out.append(f)
    return out


def _offenders() -> tuple[list[str], int]:
    found: list[str] = []
    scanned = 0
    for path in _python_files():
        try:
            # utf-8-sig, not utf-8: a BOM is a SyntaxError to ast.parse, and a file
            # carrying one runs fine while going invisible to every AST guard.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        scanned += 1
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _returns_a_value(fn):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.If) or node.orelse:
                    continue
                call = _mutating_call(node.test)
                if call and _announces_success_only(node.body):
                    found.append(
                        f"{path.name}:{node.lineno}  in {fn.name}()  "
                        f"if {call}(...): success  — no else"
                    )
    return found, scanned


def test_no_failed_operation_is_reported_by_silence() -> None:
    offenders, scanned = _offenders()

    # An empty scan is green, and green is what a broken guard looks like.
    assert scanned > 300, (
        f"only {scanned} files parsed — this guard scans core + the plugins, where "
        "there are far more. It is looking in the wrong place, so it is checking "
        "nothing and reporting success."
    )
    assert not offenders, (
        "a failed operation here prints NOTHING and exits 0 — the command looks like "
        "it worked:\n  "
        + "\n  ".join(offenders)
        + "\n\nGive the failure a branch: report it and `raise typer.Exit(1)`. If the "
        "call genuinely cannot fail, drop the `if`."
    )


def _fn(src: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))


def test_the_guard_catches_the_shape_it_exists_for() -> None:
    """Anti-vacuity: the exact #752 shape must still be detected."""
    node = next(
        n
        for n in ast.walk(
            ast.parse(
                "def cmd(x):\n"
                "    if manager.remove_trigger(x):\n"
                "        ch.success('Removed')\n"
            )
        )
        if isinstance(n, ast.If)
    )
    assert _mutating_call(node.test) == "remove_trigger"
    assert _announces_success_only(node.body) is True


def test_ordinary_conditional_reporting_is_not_flagged() -> None:
    """The real-world shapes a broader check got wrong. Flagging these would make the
    guard noise, and noise is how a guard gets deleted."""
    declined = next(
        n for n in ast.walk(ast.parse(
            "def cmd():\n"
            "    if not ch.confirm_action('Sure?'):\n"
            "        ch.info('Cancelled')\n"
        )) if isinstance(n, ast.If)
    )
    assert _mutating_call(declined.test) is None, (
        "`if not f(...)` already gives failure its own branch — flagging it would "
        "demand an else on correct code"
    )

    does_work = next(
        n for n in ast.walk(ast.parse(
            "def cmd():\n"
            "    if ch.confirm_action('Make active?'):\n"
            "        cfg.set_active_host(name)\n"
            "        ch.success('set')\n"
        )) if isinstance(n, ast.If)
    )
    assert _announces_success_only(does_work.body) is False, (
        "the body performs work, so skipping it on False may be entirely intended"
    )


def test_the_known_false_positive_is_stated_not_hidden() -> None:
    """A read-only call with a success-only body WOULD be flagged.

        if info.get("config_path"):
            ch.success("Detected …")     # nothing failed; nothing to report

    Shape alone cannot separate that from a failed mutation, and no such site exists
    in core or the plugins today (the real `host.py` case has a second statement in
    its body, so the body rule already excludes it). Pinning it here so the limit is
    a known property rather than a surprise: if this ever fires on a genuine
    read-only branch, the fix is to drop the `if` or give it an else — not to widen
    the guard until it stops meaning anything.
    """
    read_only = next(
        n for n in ast.walk(ast.parse(
            "def cmd():\n"
            "    if info.get('config_path'):\n"
            "        ch.success('Detected')\n"
        )) if isinstance(n, ast.If)
    )
    assert _mutating_call(read_only.test) == "get"
    assert _announces_success_only(read_only.body) is True


def test_helpers_are_out_of_scope() -> None:
    """A value-returning function reports failure to its caller, not via exit code."""
    helper = _fn(
        "def _try(x):\n"
        "    if db.delete(x):\n"
        "        ch.success('gone')\n"
        "    return True\n"
    )
    assert _returns_a_value(helper) is True

    command = _fn(
        "def cmd(x):\n"
        "    if db.delete(x):\n"
        "        ch.success('gone')\n"
    )
    assert _returns_a_value(command) is False
