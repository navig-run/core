"""A deliberately disabled autostart must stay disabled.

Why this test exists
--------------------
On 2026-10-22 the NAVIG brain lived on the outpost while NEURON was meant to be a
client. Its autostart task was disabled on purpose. `navig service start` — reachable
from any CLI path that decides the daemon should be running — called
:func:`_ensure_autostart_enabled`, which treats a *definite "disabled"* as a fault and
re-enables the task. Within one minute the scheduled task fired, started a second
gateway on port 8789, and NEURON and the outpost both answered ``"canonical": true``.

The exit code was 0 and the message was a cheerful success line, so nothing looked
wrong. Evidence for that incident: ``~/.navig/daemon/restart_intent.json`` (two
``navig service start`` hops) and ``~/.navig/daemon/navig-task.xml`` rewritten with
``<Enabled>true</Enabled>`` twenty-two seconds before the second hop.

The operator's disable is an instruction, not a symptom. When the opt-out marker is
present, ``start`` may start the daemon it was asked for, but it must not silently
re-arm autostart — and it must say how to undo the marker.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows autostart task only")


def test_disabled_autostart_is_not_re_enabled_when_opted_out(monkeypatch, tmp_path):
    """With the opt-out marker present, start must not call task_scheduler_enable."""
    import navig.daemon.service_manager as sm
    from navig.commands import service as svc

    marker = tmp_path / "autostart_optout"
    marker.write_text("operator decision 2026-10-22\n", encoding="utf-8")
    monkeypatch.setattr(svc, "_autostart_optout_path", lambda: marker, raising=False)

    enabled_calls: list[tuple] = []
    restart_task_calls: list[tuple] = []

    monkeypatch.setattr(sm, "task_scheduler_enabled_state", lambda: (False, True, "task is disabled"))
    monkeypatch.setattr(sm, "task_scheduler_enable", lambda *a, **k: (enabled_calls.append(a), (True, "enabled"))[1])
    monkeypatch.setattr(
        sm,
        "task_scheduler_ensure_restart_task",
        lambda *a, **k: (restart_task_calls.append(a), (True, "ok"))[1],
    )

    svc._ensure_autostart_enabled()

    assert enabled_calls == [], (
        "autostart was re-enabled even though the operator left an opt-out marker; "
        "this is the defect that produced two brains on 2026-10-22"
    )


def test_optout_marker_path_is_inside_the_daemon_dir(monkeypatch, tmp_path):
    """The marker must live next to stop_requested, so one directory holds the operator's decisions."""
    from navig.commands import service as svc

    monkeypatch.setattr("navig.daemon.supervisor._daemon_dir", lambda: tmp_path)
    p = svc._autostart_optout_path()
    assert p.parent == tmp_path
    assert p.name == "autostart_optout"


# ---------------------------------------------------------------------------
# Bypass audit (2026-10-22, independent auditor): the first fix gated only
# `_ensure_autostart_enabled`, while `service start`s launch path and
# `service restart` called `task_scheduler_enable()` directly. A client could
# therefore still be re-armed by `navig gateway restart`. These tests cover the
# single gate that every enable path must now go through.
# ---------------------------------------------------------------------------


def test_guarded_enable_does_nothing_when_opted_out(monkeypatch, tmp_path):
    import navig.daemon.service_manager as sm
    from navig.commands import service as svc

    marker = tmp_path / "autostart_optout"
    marker.write_text("opt out\n", encoding="utf-8")
    monkeypatch.setattr(svc, "_autostart_optout_path", lambda: marker, raising=False)

    calls = []
    monkeypatch.setattr(sm, "task_scheduler_enable", lambda *a, **k: (calls.append(1), (True, "ok"))[1])

    assert svc._autostart_enable_guarded() is False, "the gate must refuse while the marker exists"
    assert calls == [], "task_scheduler_enable was called past the opt-out marker"


def test_guarded_enable_enables_when_no_marker(monkeypatch, tmp_path):
    import navig.daemon.service_manager as sm
    from navig.commands import service as svc

    monkeypatch.setattr(svc, "_autostart_optout_path", lambda: tmp_path / "absent", raising=False)
    calls = []
    monkeypatch.setattr(sm, "task_scheduler_enable", lambda *a, **k: (calls.append(1), (True, "ok"))[1])

    assert svc._autostart_enable_guarded() is True
    assert calls == [1]


def test_start_and_restart_share_one_precondition_gate(monkeypatch, tmp_path):
    """Both commands must consult the same gate, or one of them re-arms the client."""
    import pytest as _pytest

    from navig.commands import service as svc

    marker = tmp_path / "autostart_optout"
    marker.write_text("opt out\n", encoding="utf-8")
    monkeypatch.setattr(svc, "_autostart_optout_path", lambda: marker, raising=False)
    monkeypatch.setattr(
        svc,
        "_read_brain_link",
        lambda: {"brainName": "outpost-server"},
        raising=False,
    )
    import typer

    # typer.Exit derives from the click Typer itself runs on, not from SystemExit, when the
    # function is called directly instead of through the CLI runner.
    with _pytest.raises((SystemExit, typer.Exit)):
        svc._enforce_start_preconditions(force=False)
    # and the same gate must let an explicit --force through
    svc._enforce_start_preconditions(force=True)


def test_restart_has_the_force_override():
    import inspect

    from navig.commands import service as svc

    params = inspect.signature(svc.service_restart).parameters
    assert "force" in params, f"service_restart has no --force override: {list(params)}"
