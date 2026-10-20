"""Two killed xdist workers must not abort the run (tests/fixtures/xdist_reschedule_guard.py).

Drives xdist's REAL LoadGroup scheduler through the sequence the gate hit twice on
2026-10-10: two workers die, two replacements are added, and the first finishes collecting
while the second is still collecting.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

xdist_loadscope = pytest.importorskip("xdist.scheduler.loadscope")
from xdist.scheduler.loadgroup import LoadGroupScheduling  # noqa: E402

from tests.fixtures import xdist_reschedule_guard  # noqa: E402

# Ten scopes so every worker always has more queued work to be handed.
IDS = [f"tests/test_m{i}.py::test_{j}" for i in range(10) for j in range(3)]


class _Node:
    def __init__(self, name: str) -> None:
        self.gateway = SimpleNamespace(id=name)
        self.shutting_down = False
        self.sent: list[list[int]] = []

    def send_runtest_some(self, indexes: list[int]) -> None:
        self.sent.append(list(indexes))

    def shutdown(self) -> None:
        self.shutting_down = True

    def __repr__(self) -> str:
        return f"<Node {self.gateway.id}>"


def _two_workers_die(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xdist_loadscope, "parse_tx_spec_config", lambda _cfg: ["popen"] * 2)
    sched = LoadGroupScheduling(SimpleNamespace(option=SimpleNamespace(loadscopereorder=True)))
    gw0, gw1 = _Node("gw0"), _Node("gw1")
    for node in (gw0, gw1):
        sched.add_node(node)
        sched.add_node_collection(node, IDS)
    sched.schedule()
    assert gw0.sent and gw1.sent, "the initial distribution must reach both workers"

    # Both die mid-unit; one replacement is up and collected, the other is still collecting.
    gw2, gw3 = _Node("gw2"), _Node("gw3")
    sched.remove_node(gw0)
    sched.add_node(gw2)
    sched.remove_node(gw1)
    sched.add_node(gw3)
    sched.add_node_collection(gw2, IDS)
    return sched, gw2, gw3


def test_unguarded_scheduler_crashes_on_the_second_replacement(monkeypatch):
    """Teeth: without the guard this IS the INTERNALERROR the gate printed."""
    monkeypatch.setattr(xdist_loadscope.LoadScopeScheduling, "_reschedule", _unguarded_reschedule())
    with pytest.raises(KeyError):  # from remove_node or schedule, whichever reschedules first
        sched, _gw2, _gw3 = _two_workers_die(monkeypatch)
        sched.schedule()


def test_guarded_scheduler_serves_each_replacement_once_it_has_collected(monkeypatch):
    assert xdist_reschedule_guard.install()
    sched, gw2, gw3 = _two_workers_die(monkeypatch)

    sched.schedule()  # crashed here before the guard
    assert gw2.sent, "the collected replacement is given work at once"
    assert not gw3.sent, "a replacement still collecting is left for its own collectionfinish"

    sched.add_node_collection(gw3, IDS)
    sched.schedule()
    assert gw3.sent, "the second replacement is served as soon as it has collected"


def test_install_is_idempotent():
    assert xdist_reschedule_guard.install()
    first = xdist_loadscope.LoadScopeScheduling._reschedule
    assert xdist_reschedule_guard.install()
    assert xdist_loadscope.LoadScopeScheduling._reschedule is first


def test_the_suite_controller_has_the_guard_installed():
    """conftest.pytest_configure installs it — a guard defined but not wired guards nothing."""
    assert getattr(
        xdist_loadscope.LoadScopeScheduling._reschedule, "_navig_reschedule_guard", False
    )


def _unguarded_reschedule():
    """xdist 3.8.0's ``_reschedule``, reproduced so the teeth test runs even with the guard on."""

    def _reschedule(self, node) -> None:  # noqa: ANN001
        if node.shutting_down:
            return
        if not self.workqueue:
            node.shutdown()
            return
        if self._pending_of(self.assigned_work[node]) > 2:
            return
        self._assign_work_unit(node)

    return _reschedule
