"""POST /api/deck/store/action — the drag-drop install / enable / remove door and
its no-restart contract.

`store.py` had NO route tests. These guard that a successful mutation invokes the
shared `notify_modules_changed` (rearm entry-point discovery + broadcast
`modules_update`), that a FAILED mutation refreshes nothing, and the input guards
(missing action, bad JSON). Same contract the /catalog/install and /bay/acquire
doors are held to — all three route through the one helper.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

import navig.gateway.deck.routes.store as store


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _spy_contract(monkeypatch):
    """Spy the two no-restart-contract primitives (rearm + SSE emit)."""
    rearms: list[bool] = []
    events: list[dict] = []
    import navig.modules.registry as reg

    monkeypatch.setattr(reg, "reset_entry_points", lambda: rearms.append(True))

    async def _fake_emit(request, payload):
        events.append(payload)

    import navig.gateway.deck.routes.modules as mod

    monkeypatch.setattr(mod, "emit_modules_update", _fake_emit)
    return rearms, events


# ── input guards ──────────────────────────────────────────────────────────────

async def test_missing_action_400():
    resp = await store.handle_deck_store_action(_Req({}))
    assert resp.status == 400


async def test_invalid_json_400():
    class _BadReq:
        async def json(self):
            raise ValueError("not json")

    resp = await store.handle_deck_store_action(_BadReq())
    assert resp.status == 400


async def test_action_without_id_400():
    # An action other than a drag-drop install needs an id.
    resp = await store.handle_deck_store_action(_Req({"action": "enable"}))
    assert resp.status == 400


# ── the no-restart contract ───────────────────────────────────────────────────

async def test_dropfolder_install_fires_contract(monkeypatch):
    class _Dest:
        name = "dropped-plugin"

        def __str__(self):
            return "/plugins/dropped-plugin"

    class _Host:
        def install(self, source):
            return _Dest()

    import navig.plugins.host as host

    monkeypatch.setattr(host, "get_plugin_host", lambda: _Host())
    rearms, events = _spy_contract(monkeypatch)
    resp = await store.handle_deck_store_action(_Req({"action": "install", "source": "/some/folder"}))
    assert resp.status == 200
    assert rearms == [True]
    assert events and events[0] == {"kind": "install", "id": "dropped-plugin"}


async def test_action_install_fires_contract(monkeypatch):
    import navig.hub as hub

    monkeypatch.setattr(hub, "apply_action", lambda item_id, action: {"ok": True})
    rearms, events = _spy_contract(monkeypatch)
    resp = await store.handle_deck_store_action(_Req({"action": "install", "id": "some-mod"}))
    assert resp.status == 200
    assert rearms == [True]
    assert events and events[0] == {"kind": "install", "id": "some-mod"}


async def test_failed_action_fires_no_contract(monkeypatch):
    import navig.hub as hub

    monkeypatch.setattr(hub, "apply_action", lambda item_id, action: {"ok": False, "error": "nope"})
    rearms, events = _spy_contract(monkeypatch)
    resp = await store.handle_deck_store_action(_Req({"action": "install", "id": "x"}))
    assert resp.status == 400
    assert rearms == [] and events == []  # failed mutation → nothing rearmed / refreshed
