"""Regression: the board-lane approve/reject drops the pending Inbox approval ask for the card.

When a card runs in approval mode it parks awaiting_approval and _surface_card_approval_ask posts
an Inbox "Approve" question (source=`card:<id>`). If the operator instead approves/rejects from the
board Agent lane, that lane resolves the card OUTSIDE the ask — so the ask must be dismissed, else
answering the stale ask later re-runs _on_answer → move + cascade again (double-running auto_advance
dependents).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class _Reg:
    def __init__(self):
        self.dismissed: list[str] = []

    async def replace_pending_by_source(self, source: str) -> int:
        self.dismissed.append(source)
        return 0


class _Gateway:
    def __init__(self):
        self.request_registry = _Reg()
        self.system_events = None  # _emit_update no-ops without an emit()


class _App:
    def __init__(self, gw):
        self._gw = gw

    def get(self, key):
        return self._gw if key == "gateway" else None


class _Req:
    def __init__(self, card_id: str, gw):
        self.match_info = {"id": card_id}
        self.app = _App(gw)


class _Store:
    """Minimal board store — card exists, moves succeed, no dependents to cascade."""

    def get_card(self, cid):
        return {"id": cid, "stage": "agent", "agent_status": "awaiting_approval"}

    def update_card(self, cid, patch):
        return {"id": cid, **patch}

    def move_card(self, cid, stage, actor="user"):
        return {"id": cid, "stage": stage}

    def unlock_after_done(self, cid):
        return []


async def test_approve_dismisses_pending_card_ask(monkeypatch):
    import navig.gateway.deck.routes.board as board

    monkeypatch.setattr(board, "_store", lambda: _Store())
    gw = _Gateway()
    resp = await board.handle_board_card_approve(_Req("c1", gw))
    assert resp.status == 200
    assert gw.request_registry.dismissed == ["card:c1"]  # stale ask dropped


async def test_reject_dismisses_pending_card_ask(monkeypatch):
    import navig.gateway.deck.routes.board as board

    monkeypatch.setattr(board, "_store", lambda: _Store())
    gw = _Gateway()
    resp = await board.handle_board_card_reject(_Req("c1", gw))
    assert resp.status == 200
    assert gw.request_registry.dismissed == ["card:c1"]


async def test_dismiss_is_best_effort_without_registry(monkeypatch):
    import navig.gateway.deck.routes.board as board

    monkeypatch.setattr(board, "_store", lambda: _Store())

    class _BareGateway:
        system_events = None  # no request_registry attribute at all

    resp = await board.handle_board_card_approve(_Req("c1", _BareGateway()))
    assert resp.status == 200  # a missing registry must not fail the approval
