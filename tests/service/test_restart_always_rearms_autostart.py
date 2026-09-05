"""`navig service restart` must never leave autostart switched off.

`service_restart` disables the scheduled task before killing the daemon, so
`RestartOnFailure` cannot respawn it mid-restart. That is correct. What was not
correct is where it turned the task back on: the re-enable sat inside
``if _started:``, i.e. it only ran when the daemon was observed within the
10-second poll window.

So a restart that took a moment longer than the poll -- and "Gateway ready in
17.23s" is a real measurement from the operator's machine -- exited with an error
and left the task DISABLED. Permanently, and silently. The daemon usually came up
a second later, producing the exact state found repeatedly on that install:

    Daemon process: RUNNING
    Task Scheduler: DISABLED

i.e. nothing would restart the daemon after a reboot or a crash. That is how the
autostart came to be off on the morning the daily check-ins were investigated, and
it happened AGAIN twice during the same session while the watchdog was being
verified.

The same hole existed on the "Couldn't stop the daemon" path, which raised out of
the function between the disable and the enable.

The invariant these tests pin: **between the `task_scheduler_disable()` call and
the end of `service_restart`, every exit path re-enables the task.** A deliberate
`navig service stop` is a different command and still leaves it off, by design.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from navig.commands import service as service_cmd

DISABLE = "task_scheduler_disable"
ENABLE = "task_scheduler_enable"


def _restart_tree() -> ast.AST:
    source = inspect.getsource(service_cmd.service_restart)
    return ast.parse(inspect.cleandoc(source))


def _call_lines(tree: ast.AST, name: str) -> list[int]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = getattr(func, "id", None) or getattr(func, "attr", None)
            if called == name:
                out.append(node.lineno)
    return sorted(out)


def _exit_lines(tree: ast.AST) -> list[int]:
    """Lines of `raise typer.Exit(...)` -- the ways this function abandons ship."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            if getattr(func, "attr", None) == "Exit" or getattr(func, "id", None) == "Exit":
                out.append(node.lineno)
    return sorted(out)


def test_restart_disables_the_task_at_all() -> None:
    """Anti-vacuity: if the disable is gone, the rest of this file proves nothing."""
    tree = _restart_tree()
    assert _call_lines(tree, DISABLE), (
        f"service_restart no longer calls {DISABLE}() -- if that is deliberate, delete "
        "this file; otherwise the tests below are checking an invariant that has no "
        "subject"
    )


def test_every_exit_after_the_disable_rearms_autostart() -> None:
    """THE INVARIANT. A restart must not end with autostart switched off.

    Checked on the AST rather than by reading the happy path, because the defect was
    an exit path nobody was looking at: the re-enable was real, reachable, and simply
    not on the branch that mattered.
    """
    tree = _restart_tree()
    disable_at = min(_call_lines(tree, DISABLE))
    enables = _call_lines(tree, ENABLE)
    assert enables, f"service_restart never calls {ENABLE}() -- autostart stays off"

    unguarded = [
        line
        for line in _exit_lines(tree)
        if line > disable_at and not any(disable_at < e < line for e in enables)
    ]
    assert not unguarded, (
        "these exit paths leave the scheduled task DISABLED (lines are relative to "
        f"service_restart): {unguarded}. The task was switched off at line {disable_at} "
        f"and {ENABLE}() runs at {enables}. Re-arm before bailing out, or the operator "
        "keeps a running daemon that nothing will ever restart."
    )


def test_the_final_rearm_is_not_conditional_on_a_successful_start() -> None:
    """The original bug, pinned by shape.

    The re-enable used to live inside `if _started:`. Assert the LAST enable call is
    not nested inside a conditional that tests the start result -- a restart that
    failed still has to leave autostart armed, because it disabled it.
    """
    tree = _restart_tree()
    last_enable = max(_call_lines(tree, ENABLE))

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.dump(node.test)
        if "_started" not in test_src:
            continue
        body_lines = {n.lineno for stmt in node.body for n in ast.walk(stmt) if hasattr(n, "lineno")}
        assert last_enable not in body_lines, (
            "the final task_scheduler_enable() is inside `if _started:` again -- a "
            "restart whose daemon appeared after the poll window will leave autostart "
            "disabled, which is how the operator ended up RUNNING but unrecoverable"
        )


def test_service_stop_still_leaves_it_disabled() -> None:
    """The deliberate case must NOT be 'fixed'.

    `navig service stop` means stop, and keeping the task off is what makes the stop
    stick. Only `restart` has to re-arm.
    """
    source = inspect.getsource(service_cmd.service_stop)
    tree = ast.parse(inspect.cleandoc(source))
    assert _call_lines(tree, DISABLE), "service_stop no longer disables the task"
    assert not _call_lines(tree, ENABLE), (
        "service_stop re-enables the task, so a deliberate stop can be undone by the "
        "next trigger -- that is not a stop"
    )


def test_the_guard_reads_the_file_it_thinks_it_reads() -> None:
    """Cheap floor: inspect.getsource must return real code, not an empty stub."""
    source = inspect.getsource(service_cmd.service_restart)
    assert len(source.splitlines()) > 20, "service_restart source looks truncated"
    assert Path(service_cmd.__file__).name == "service.py"
