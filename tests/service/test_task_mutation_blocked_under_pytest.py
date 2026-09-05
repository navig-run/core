"""A test process must never change the operator's real scheduled task.

`TASK_NAME` is the bare literal ``"NAVIG Daemon"``. There is no config-dir scoping
on Task Scheduler the way there now is on the daemon's pid file and log dir (#1189,
#1202), **and there cannot be**: the task is a machine-level object, not a file under
a config root. So any code path that reaches ``schtasks /change ... /disable`` from a
test disables the autostart of whoever is running the suite — silently, and it stays
off until someone notices their daemon never came back from a reboot. That is exactly
the two-day outage this line of work started from.

**Measured before adding the guard: the full `tests/service` suite (110 tests) leaves
the task `Ready`.** So this is a floor placed BEFORE something falls through it, not a
bug report — the honest framing, because a guard sold as fixing a live defect that
nobody can reproduce is how exemptions later get waved through.

What makes it worth placing anyway: **nine tests invoke `service stop` / `restart` /
`uninstall` without stubbing these helpers**, and `service_restart` calls
`task_scheduler_disable()` as its very first action on Windows. They avoid it today
through the particular stubs they happen to install. That is one refactor away from
disarming a developer's autostart on every test run, and the failure is invisible.

Reads are deliberately NOT guarded: observing the machine harms nothing, and several
tests legitimately drive `task_scheduler_status` / `_health` with a stubbed subprocess.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from navig.daemon import service_manager as sm

MUTATORS = [
    "task_scheduler_install",
    "task_scheduler_end",
    "task_scheduler_disable",
    "task_scheduler_enable",
    "task_scheduler_uninstall",
]
READERS = ["task_scheduler_status", "task_scheduler_health", "task_scheduler_enabled_state"]


@pytest.fixture
def _no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record any attempt to actually shell out, so a leak cannot pass quietly."""
    calls: list = []

    def _boom(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        raise AssertionError(
            f"a mutating Task Scheduler call escaped the guard and ran: {calls[-1]}"
        )

    monkeypatch.setattr(sm.subprocess, "run", _boom)
    return calls


@pytest.mark.parametrize("name", MUTATORS)
def test_a_mutating_op_refuses_and_never_shells_out(name: str, _no_subprocess) -> None:
    """The protection that matters: schtasks is never reached at all."""
    ok, detail = getattr(sm, name)()

    assert ok is False, f"{name}() reported success from a test process"
    assert not _no_subprocess, f"{name}() ran a subprocess despite the guard"
    assert "test process" in detail, f"{name}() gave no reason: {detail!r}"
    assert "NAVIG_ALLOW_TASK_MUTATION" in detail, "the refusal must say how to opt in"


@pytest.mark.parametrize("name", MUTATORS)
def test_the_opt_in_clears_the_refusal(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that genuinely means to drive Task Scheduler must still be able to.

    A guard with no escape hatch gets deleted the first time someone needs it.
    """
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    assert sm._refuse_task_mutation(name) is None


def test_with_the_opt_in_the_call_actually_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """...and the guard is genuinely the only thing that was stopping it.

    Uses `disable` deliberately: it is the one mutator whose body is nothing but the
    subprocess call, so proving it reaches `schtasks` needs no filesystem stubbing.
    An earlier version of this test parametrised over ALL mutators and had to stub
    `_ensure_dirs`/`_schtasks_xml` for `install` -- which left `daemon_dir()` real and
    would have written a stub XML over the operator's `navig-task.xml`. Demonstrating
    this file's own hazard while testing the fix for it is not the assertion to keep.
    """
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    reached: list = []

    class _Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr(sm.subprocess, "run", lambda *a, **k: reached.append(a[0]) or _Completed())
    ok, _detail = sm.task_scheduler_disable()

    assert reached, "the opt-in did not let the call through"
    assert "/disable" in reached[0], f"reached schtasks with the wrong verb: {reached[0]}"
    assert ok is True


def test_production_is_not_affected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The floor: outside a test process the guard must be invisible.

    A protection that also blocks the real daemon would be worse than the hazard.
    """
    monkeypatch.delitem(__import__("sys").modules, "pytest", raising=False)
    assert sm._refuse_task_mutation("disable") is None


@pytest.mark.parametrize("name", READERS)
def test_reads_are_not_guarded(name: str) -> None:
    """Observing the machine harms nothing and several tests rely on it."""
    source = inspect.getsource(getattr(sm, name))
    assert "_refuse_task_mutation" not in source, (
        f"{name} is a READ -- guarding it breaks tests that legitimately drive it with "
        "a stubbed subprocess, and it cannot harm the operator's machine"
    )


def test_every_mutating_op_carries_the_guard() -> None:
    """The durable half: a mutating op added tomorrow must not slip through.

    Discovered from the source rather than compared against this file's list, so a new
    `schtasks /create|/change|/delete|/run|/end` helper fails here instead of quietly
    reaching the operator's task.
    """
    source = Path(sm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    MUTATING_FLAGS = ("/create", "/change", "/delete", "/run", "/end")
    unguarded: list[str] = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(source, fn) or ""
        if "schtasks" not in body:
            continue
        if not any(flag in body for flag in MUTATING_FLAGS):
            continue  # a read
        if "_refuse_task_mutation" not in body:
            unguarded.append(fn.name)

    assert not unguarded, (
        "these functions run a MUTATING schtasks command with no test-process guard, so "
        f"a test can change the operator's real autostart: {unguarded}. Add "
        "`refusal = _refuse_task_mutation('<verb>')` as the first statement."
    )


def test_the_detector_would_notice_an_unguarded_op() -> None:
    """Anti-vacuity for the guard above -- prove the scan can actually flag something."""
    sample = (
        "def task_scheduler_wipe():\n"
        "    subprocess.run(['schtasks', '/delete', '/tn', TASK_NAME, '/f'])\n"
    )
    tree = ast.parse(sample)
    found = [
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and "schtasks" in (ast.get_source_segment(sample, fn) or "")
        and "/delete" in (ast.get_source_segment(sample, fn) or "")
        and "_refuse_task_mutation" not in (ast.get_source_segment(sample, fn) or "")
    ]
    assert found == ["task_scheduler_wipe"], "the detector cannot see an unguarded op"
