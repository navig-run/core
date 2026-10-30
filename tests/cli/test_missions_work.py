"""`navig missions work` — the operator's handle on the distributed queue.

The command is invoked as a function rather than through ``CliRunner`` on the sub-app: a Typer
app with a single command resolves the verb itself, so ``invoke(app, ["work", ...])`` fails with
"unexpected extra argument". The option surface is covered by the end-to-end smoke run through
the real entry point (``py -m navig missions work``); what these tests pin is the behaviour and
the exit codes, which is where the operator actually gets hurt.
"""

from __future__ import annotations

import json

import pytest
import typer

from navig.commands import missions as missions_cmd
from navig.missions.claimer import ClaimOutcome


class _Claimer:
    def __init__(self, outcome: ClaimOutcome) -> None:
        self._outcome = outcome
        self.calls = 0

    async def run_once(self) -> ClaimOutcome:
        self.calls += 1
        return self._outcome


def _install(monkeypatch, outcome: ClaimOutcome) -> _Claimer:
    claimer = _Claimer(outcome)
    monkeypatch.setattr(missions_cmd, "_transport", lambda url, token: object())
    monkeypatch.setattr(
        "navig.missions.claimer.MissionClaimer", lambda transport, node_id: claimer
    )
    monkeypatch.setattr(missions_cmd, "_config_value", lambda key, default="": "")
    return claimer


def _work(**kwargs):
    params = {"node_id": "node-a", "url": "", "token": "", "once": True,
              "interval": 30.0, "json_output": False}
    params.update(kwargs)
    return missions_cmd.missions_work(**params)


def test_the_command_is_registered_under_missions():
    """A Typer app nothing registers is dead code — this is how `deploy init` once vanished."""
    from navig.cli import registration

    assert registration._EXTERNAL_CMD_MAP["missions"] == (
        "navig.commands.missions",
        "missions_app",
    )


def test_idle_queue_exits_zero(monkeypatch, capsys):
    _install(monkeypatch, ClaimOutcome(node_id="node-a"))

    with pytest.raises(typer.Exit) as excinfo:
        _work()

    assert excinfo.value.exit_code == 0
    assert "no queued mission" in capsys.readouterr().out


def test_a_successful_mission_prints_mission_and_receipt(monkeypatch, capsys):
    _install(
        monkeypatch,
        ClaimOutcome(
            node_id="node-a",
            claimed=True,
            mission_id="m1",
            capability="readiness.probe",
            executed=True,
            succeeded=True,
            receipt_id="r-1",
        ),
    )

    with pytest.raises(typer.Exit) as excinfo:
        _work()

    out = capsys.readouterr().out
    assert excinfo.value.exit_code == 0
    assert "m1" in out and "r-1" in out


def test_a_failed_mission_exits_non_zero_and_names_the_reason(monkeypatch, capsys):
    _install(
        monkeypatch,
        ClaimOutcome(
            node_id="node-a",
            claimed=True,
            mission_id="m1",
            capability="teleport",
            succeeded=False,
            error="this node cannot execute capability 'teleport'",
            receipt_id="r-2",
        ),
    )

    with pytest.raises(typer.Exit) as excinfo:
        _work()

    assert excinfo.value.exit_code == 1
    assert "teleport" in capsys.readouterr().out


def test_json_mode_emits_one_parseable_object(monkeypatch, capsys):
    _install(monkeypatch, ClaimOutcome(node_id="node-a"))

    with pytest.raises(typer.Exit):
        _work(json_output=True)

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["claimed"] is False
    assert payload["node_id"] == "node-a"
