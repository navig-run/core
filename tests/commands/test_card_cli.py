"""``navig card`` — role-gated lifecycle, claims and dependencies on the shared board.

Pins the coordination rules that the HTTP surface deliberately does not do:
the caller's registry role decides which stage moves exist, a card has exactly
one claimant, an unclaimed shell is refused with the command that fixes it, and
the CLI's canonical stage names translate to (and never invent beyond)
``DEFAULT_STAGES``.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import navig.store.board as board_mod
from navig import agent_registry
from navig.commands.card import card_app
from navig.store.board import BoardStore

runner = CliRunner()

_SESS = "11111111-1111-1111-1111-111111111111"
_SESS_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESS)
    monkeypatch.setattr(board_mod, "_store", BoardStore(tmp_path / "board.db"))
    agent_registry.claim("orchestrator", session_id=_SESS)


def _card(title: str = "seeded") -> dict:
    return board_mod.get_board_store().create_card(title)


def test_add_needs_a_claimed_session(monkeypatch):
    monkeypatch.setattr(agent_registry, "find_mine", lambda: None)
    res = runner.invoke(card_app, ["add", "orphan"])
    assert res.exit_code == 1
    assert "needs a claimed role" in res.output
    assert "mesh agents claim" in res.output


def test_add_is_orchestrator_only():
    agent_registry.claim("backend-worker", session_id=_SESS)
    res = runner.invoke(card_app, ["add", "worker grab"])
    assert res.exit_code == 1
    assert "orchestrator" in res.output


def test_orchestrator_add_and_list_json():
    res = runner.invoke(
        card_app,
        ["add", "Wire the ledger", "--space", "schema", "--notes", "record: .navig/tasks/x.md", "--json"],
    )
    assert res.exit_code == 0
    row = json.loads(res.output)
    assert row["canonical_stage"] == "planned"
    assert row["stage"] == "backlog"
    assert row["space"] == "schema"

    listed = json.loads(runner.invoke(card_app, ["list", "--json"]).output)
    assert listed["count"] == 1
    assert listed["cards"][0]["id"] == row["id"]

    only = json.loads(runner.invoke(card_app, ["list", "--stage", "planned", "--json"]).output)
    assert only["count"] == 1
    none = json.loads(runner.invoke(card_app, ["list", "--stage", "done", "--json"]).output)
    assert none["count"] == 0


def test_unknown_stage_is_refused_not_invented():
    c = _card()
    res = runner.invoke(card_app, ["move", c["id"], "--to", "someday"])
    assert res.exit_code == 1
    assert "Unknown stage" in res.output
    assert board_mod.get_board_store().get_card(c["id"])["stage"] == "backlog"


def test_worker_matrix_takes_work_and_submits_only():
    c = _card()
    agent_registry.claim("backend-worker", session_id=_SESS)

    take = runner.invoke(card_app, ["move", c["id"], "--to", "active"])
    assert take.exit_code == 0
    assert board_mod.get_board_store().get_card(c["id"])["stage"] == "in_progress"

    submit = runner.invoke(card_app, ["move", c["id"], "--to", "review"])
    assert submit.exit_code == 0
    assert board_mod.get_board_store().get_card(c["id"])["stage"] == "agent"

    land = runner.invoke(card_app, ["move", c["id"], "--to", "done"])
    assert land.exit_code == 1
    assert "backend-worker may not move" in land.output
    assert board_mod.get_board_store().get_card(c["id"])["stage"] == "agent"


def test_reviewer_moves_no_cards():
    c = _card()
    agent_registry.claim("reviewer", session_id=_SESS)
    res = runner.invoke(card_app, ["move", c["id"], "--to", "active"])
    assert res.exit_code == 1
    assert "reviewer" in res.output
    assert board_mod.get_board_store().get_card(c["id"])["stage"] == "backlog"


def test_orchestrator_lands_review_done():
    c = _card()
    board_mod.get_board_store().move_card(c["id"], "agent", actor="agent")
    res = runner.invoke(card_app, ["move", c["id"], "--to", "done"])
    assert res.exit_code == 0
    card = board_mod.get_board_store().get_card(c["id"])
    assert card["stage"] == "done" and card["completed_at"]


def test_claim_is_exclusive_across_sessions_and_force_is_orchestrator_only(monkeypatch):
    c = _card()
    assert runner.invoke(card_app, ["claim", c["id"]]).exit_code == 0

    # A second live session, registry-claimed as a worker, loses the CAS.
    agent_registry.claim("frontend-worker", session_id=_SESS_B)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESS_B)
    blocked = runner.invoke(card_app, ["claim", c["id"]])
    assert blocked.exit_code == 1
    assert "not free" in blocked.output

    forced = runner.invoke(card_app, ["release", c["id"], "--force"])
    assert forced.exit_code == 1              # a worker never adjudicates

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESS)
    force = runner.invoke(card_app, ["release", c["id"], "--force"])
    assert force.exit_code == 0
    assert board_mod.get_board_store().get_card(c["id"])["origin_ref"] is None


def _registry_card(session_id: str):
    return next(
        e for e in agent_registry.load()["agents"] if e["session_id"] == session_id
    ).get("card")


def test_claim_and_release_keep_the_agent_registry_in_step():
    """`navig mesh agents list` reads the registry: it must show the card a claim took."""
    c = _card()
    assert runner.invoke(card_app, ["claim", c["id"]]).exit_code == 0
    assert _registry_card(_SESS) == c["id"]
    assert runner.invoke(card_app, ["release", c["id"]]).exit_code == 0
    assert _registry_card(_SESS) is None


def test_a_claim_the_registry_cannot_record_is_undone(monkeypatch):
    c = _card()

    def _boom(*_a, **_k):
        raise OSError("registry is read-only")

    monkeypatch.setattr(agent_registry, "set_card", _boom)
    res = runner.invoke(card_app, ["claim", c["id"]])
    assert res.exit_code == 1
    assert board_mod.get_board_store().get_card(c["id"])["origin_ref"] is None


def test_release_of_a_card_you_do_not_hold_is_an_error():
    c = _card()
    res = runner.invoke(card_app, ["release", c["id"]])
    assert res.exit_code == 1
    assert "does not hold" in res.output


def test_deps_wiring_refuses_cycles_and_self_edges():
    a, b = _card("a"), _card("b")
    ok = runner.invoke(card_app, ["deps", "add", a["id"], "--on", b["id"]])
    assert ok.exit_code == 0
    assert b["id"] in board_mod.get_board_store().get_card(a["id"])["deps"]

    cycle = runner.invoke(card_app, ["deps", "add", b["id"], "--on", a["id"]])
    assert cycle.exit_code == 1

    self_edge = runner.invoke(card_app, ["deps", "add", a["id"], "--on", a["id"]])
    assert self_edge.exit_code == 1

    gone = runner.invoke(card_app, ["deps", "remove", a["id"], "--from", b["id"]])
    assert gone.exit_code == 0
    assert b["id"] not in board_mod.get_board_store().get_card(a["id"])["deps"]
    twice = runner.invoke(card_app, ["deps", "remove", a["id"], "--from", b["id"]])
    assert twice.exit_code == 1


def test_an_exact_todo_id_is_not_a_card():
    """`get_card` reads the shared table: a todo's exact id must not resolve as a card."""
    a = _card("a")
    todo = board_mod.get_board_store().create_todo("Buy milk")
    res = runner.invoke(card_app, ["deps", "add", a["id"], "--on", todo["id"]])
    assert res.exit_code == 1
    assert board_mod.get_board_store().get_card(a["id"])["deps"] == []


def test_subtask_role_gates():
    c = _card()
    add = runner.invoke(card_app, ["subtask", "add", c["id"], "gate passes"])
    assert add.exit_code == 0
    sub = board_mod.get_board_store().card_subtasks(c["id"])[0]

    agent_registry.claim("reviewer", session_id=_SESS)
    review_tick = runner.invoke(card_app, ["subtask", "done", sub["id"]])
    assert review_tick.exit_code == 1
    assert "receipt" in review_tick.output

    agent_registry.claim("backend-worker", session_id=_SESS)
    tick = runner.invoke(card_app, ["subtask", "done", sub["id"]])
    assert tick.exit_code == 0
    assert board_mod.get_board_store().card_subtasks(c["id"])[0]["done"] is True


def test_show_json_carries_checklist_and_history():
    c = _card()
    board_mod.get_board_store().move_card(c["id"], "in_progress", actor="agent")
    board_mod.get_board_store().add_subtask(c["id"], "checked")
    res = runner.invoke(card_app, ["show", c["id"], "--json"])
    assert res.exit_code == 0
    row = json.loads(res.output)
    assert row["canonical_stage"] == "active"
    assert [s["title"] for s in row["subtasks"]] == ["checked"]
    assert row["history"] and row["history"][0]["to_stage"] == "in_progress"


def test_move_into_work_is_refused_while_dependencies_wait():
    a, b = _card("a"), _card("b")
    assert runner.invoke(card_app, ["deps", "add", a["id"], "--on", b["id"]]).exit_code == 0
    assert board_mod.get_board_store().get_card(a["id"])["gate"] == "waiting"
    agent_registry.claim("backend-worker", session_id=_SESS)

    res = runner.invoke(card_app, ["move", a["id"], "--to", "active"])
    assert res.exit_code == 1
    assert "waiting on unfinished dependencies" in res.output
    assert board_mod.get_board_store().get_card(a["id"])["stage"] == "backlog"

    # Once the dependency is done, the same move goes through.
    board_mod.get_board_store().move_card(b["id"], "done", actor="agent")
    assert runner.invoke(card_app, ["move", a["id"], "--to", "active"]).exit_code == 0
