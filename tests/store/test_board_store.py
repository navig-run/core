"""BoardStore — the store the Tasks/Goals board runs on.

The module `navig.store.board` was referenced by `routes/board.py` but never
committed, so `/api/deck/board` 500'd with `No module named 'navig.store.board'`
and both apps showed "Couldn't load your board". These tests pin the contract
the route depends on: the derived `gate`/`deps`, the cycle-safe dependency DAG,
the unlock cascade, ai_mode resolution, and that the route's own `_store()` and
snapshot handler now resolve.
"""

from __future__ import annotations

import pytest

from navig.store.board import BoardStore


@pytest.fixture
def store(tmp_path):
    return BoardStore(tmp_path / "board.db")


def test_empty_snapshot_has_every_key_the_frontend_reads(store):
    snap = store.snapshot()
    assert set(snap) == {"goals", "cards", "subtasks", "stages", "settings"}
    assert snap["goals"] == [] and snap["cards"] == []
    assert snap["settings"]["default_ai_mode"] in ("draft", "approval", "auto")
    assert [s["key"] for s in snap["stages"]] == ["backlog", "in_progress", "agent", "done"]


def test_goal_crud(store):
    g = store.create_goal("Ship v1", description="d", color="#abc")
    assert g["id"] and g["title"] == "Ship v1" and g["status"] == "active"
    assert store.get_goal(g["id"])["description"] == "d"
    assert store.update_goal(g["id"], {"title": "Ship v2", "bogus": "ignored"})["title"] == "Ship v2"
    assert store.update_goal("nope", {"title": "x"}) is None  # 404 signal
    store.delete_goal(g["id"])
    assert store.get_goal(g["id"]) is None


def test_deleting_a_goal_orphans_its_cards_not_deletes_them(store):
    g = store.create_goal("G")
    c = store.create_card("task", goal_id=g["id"])
    store.delete_goal(g["id"])
    assert store.get_card(c["id"]) is not None          # card survives
    assert store.get_card(c["id"])["goal_id"] is None   # ON DELETE SET NULL


def test_card_defaults_and_gate_ready_with_no_deps(store):
    c = store.create_card("solo")
    assert c["stage"] == "backlog" and c["agent_status"] == "idle"
    assert c["ai_mode"] == "inherit" and c["auto_advance"] is False
    assert c["gate"] == "ready" and c["deps"] == []


def test_dependency_gate_and_unlock_cascade(store):
    a = store.create_card("design")
    b = store.create_card("build")
    assert store.add_dependency(b["id"], a["id"]) is True
    assert store.get_card(b["id"])["gate"] == "waiting"        # b waits for a
    assert store.get_card(b["id"])["deps"] == [a["id"]]
    assert store.get_card(a["id"])["gate"] == "ready"          # a has no deps

    store.move_card(a["id"], "done", actor="user")
    assert store.get_card(b["id"])["gate"] == "ready"          # a done → b ready
    unlocked = store.unlock_after_done(a["id"])
    assert [u["id"] for u in unlocked] == [b["id"]]


def test_dependency_rejects_self_edge_and_cycles(store):
    a = store.create_card("a")
    b = store.create_card("b")
    c = store.create_card("c")
    assert store.add_dependency(a["id"], a["id"]) is False      # self
    assert store.add_dependency(b["id"], a["id"]) is True       # b→a
    assert store.add_dependency(c["id"], b["id"]) is True       # c→b
    assert store.add_dependency(a["id"], c["id"]) is False      # a→c would cycle a→c→b→a
    assert store.add_dependency("ghost", a["id"]) is False      # unknown card


def test_remove_dependency_reopens_the_gate(store):
    a = store.create_card("a")
    b = store.create_card("b")
    store.add_dependency(b["id"], a["id"])
    assert store.get_card(b["id"])["gate"] == "waiting"
    store.remove_dependency(b["id"], a["id"])
    assert store.get_card(b["id"])["gate"] == "ready"
    assert store.get_card(b["id"])["deps"] == []


def test_create_chain_wires_a_pipeline(store):
    chain = store.create_chain([{"title": "one"}, {"title": "two"}, {"title": "three"}])
    assert [c["title"] for c in chain] == ["one", "two", "three"]
    assert chain[0]["deps"] == [] and chain[0]["gate"] == "ready"
    assert chain[1]["deps"] == [chain[0]["id"]] and chain[1]["gate"] == "waiting"
    assert chain[2]["deps"] == [chain[1]["id"]]


def test_move_to_terminal_sets_completed_at_and_records_history(store):
    c = store.create_card("x")
    assert store.get_card(c["id"])["completed_at"] is None
    store.move_card(c["id"], "done", actor="agent")
    assert store.get_card(c["id"])["completed_at"] is not None
    store.move_card(c["id"], "in_progress", actor="user")
    assert store.get_card(c["id"])["completed_at"] is None   # cleared on leaving
    hist = store.recent_history(20)
    assert len(hist) == 2 and hist[0]["to_stage"] == "in_progress" and hist[0]["actor"] == "user"


def test_resolve_ai_mode_falls_back_to_the_board_default(store):
    assert store.resolve_ai_mode("inherit") == "approval"     # default default
    assert store.resolve_ai_mode(None) == "approval"
    assert store.resolve_ai_mode("auto") == "auto"            # concrete passes through
    store.save_settings({"default_ai_mode": "draft"})
    assert store.resolve_ai_mode("inherit") == "draft"        # follows the setting


def test_settings_merge_is_partial_and_ignores_junk(store):
    store.save_settings({"default_auto_advance": True})
    s = store.get_settings()
    assert s["default_auto_advance"] is True
    assert s["default_ai_mode"] == "approval"                 # untouched key kept
    store.save_settings({"default_ai_mode": "nonsense"})
    assert store.get_settings()["default_ai_mode"] == "approval"  # invalid rejected


def test_update_card_only_writes_allowlisted_columns(store):
    c = store.create_card("x")
    updated = store.update_card(c["id"], {
        "title": "renamed", "agent_status": "running", "auto_advance": True,
        "id": "HACK", "created_at": "1999", "gate": "ready",  # all ignored
    })
    assert updated["title"] == "renamed" and updated["agent_status"] == "running"
    assert updated["auto_advance"] is True
    assert updated["id"] == c["id"]                           # id untouched
    assert store.update_card("nope", {"title": "x"}) is None  # 404 signal


def test_subtasks(store):
    c = store.create_card("x")
    st = store.add_subtask(c["id"], "sketch")
    assert st["done"] is False and st["card_id"] == c["id"]
    assert store.update_subtask(st["id"], {"done": True})["done"] is True
    assert store.snapshot()["subtasks"][0]["done"] is True
    store.delete_subtask(st["id"])
    assert store.snapshot()["subtasks"] == []


def test_route_store_singleton_and_handler_resolve(tmp_path, monkeypatch):
    """The exact seam that was broken: routes/board.py:_store() imports the
    store, and the snapshot handler runs without the missing-module 500."""
    pytest.importorskip("aiohttp")
    import navig.gateway.deck.routes.board as route
    import navig.store.board as board_mod

    monkeypatch.setattr(board_mod, "_store", None, raising=False)
    monkeypatch.setattr(board_mod, "_default_db_path", lambda: tmp_path / "board.db")

    s = route._store()                       # the call that raised ModuleNotFoundError
    assert isinstance(s, BoardStore)
    assert route._store() is s               # module singleton
    assert set(s.snapshot()) == {"goals", "cards", "subtasks", "stages", "settings"}
