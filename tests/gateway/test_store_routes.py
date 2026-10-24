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


# ── the versioned envelope NAVIG OS reads ─────────────────────────────────────


class _GetReq:
    """Enough of a request for the READ endpoint (it reads `.query`, not `.json`)."""

    query: dict = {}


def _body(resp) -> dict:
    import json

    return json.loads(resp.body.decode("utf-8"))


async def test_the_store_read_carries_the_api_version(monkeypatch):
    """NAVIG OS must be able to tell a core that understands it from one that does not.

    The envelope rides the endpoint the OS already polls rather than a second `/v1`
    route — a parallel path would be a second surface to keep in step, and the
    catalog would then exist twice.
    """
    import navig.hub as hub

    class _Item:
        def to_dict(self):
            return {"id": "demo"}

    monkeypatch.setattr(hub, "collect_store", lambda **kwargs: [_Item()])
    monkeypatch.setattr(hub, "store_status", lambda items: {"total": len(items)})

    resp = await store.handle_deck_store(_GetReq())
    assert resp.status == 200
    assert _body(resp)["api_version"] == store.STORE_API_VERSION
    assert _body(resp)["items"] == [{"id": "demo"}]  # additive only — nothing moved


async def test_mutations_carry_the_same_version(monkeypatch):
    """A client reading the version must see it on writes too, or the check it
    performs on the response is only half true."""
    import navig.hub as hub

    monkeypatch.setattr(hub, "apply_action", lambda item_id, action: {"ok": True})
    _spy_contract(monkeypatch)

    resp = await store.handle_deck_store_action(_Req({"action": "enable", "id": "demo"}))
    assert resp.status == 200
    assert _body(resp)["api_version"] == store.STORE_API_VERSION


async def test_an_error_response_carries_it_too():
    """Otherwise a failure is indistinguishable from a core too old to know the field."""
    resp = await store.handle_deck_store_action(_Req({"action": "enable"}))
    assert resp.status == 400
    assert _body(resp)["api_version"] == store.STORE_API_VERSION


def test_the_version_is_pinned_not_floating():
    """A version that drifts on its own is worse than none: every consumer that
    asserted `== 1` would start failing on an additive release."""
    assert store.STORE_API_VERSION == 1


async def test_failed_action_fires_no_contract(monkeypatch):
    import navig.hub as hub

    monkeypatch.setattr(hub, "apply_action", lambda item_id, action: {"ok": False, "error": "nope"})
    rearms, events = _spy_contract(monkeypatch)
    resp = await store.handle_deck_store_action(_Req({"action": "install", "id": "x"}))
    assert resp.status == 400
    assert rearms == [] and events == []  # failed mutation → nothing rearmed / refreshed


# ── the capability index (additive, ?capabilities=1) ─────────────────────────


class _Cap:
    def __init__(self):
        self.id = "perplexity"

    def to_dict(self):
        return {"id": self.id, "backends": []}


async def test_capabilities_flag_is_off_by_default(monkeypatch):
    """A v1 client that never heard of the flag must see the exact old payload."""
    import navig.hub as hub

    monkeypatch.setattr(hub, "collect_store", lambda **kwargs: [])
    monkeypatch.setattr(hub, "store_status", lambda items: {"total": 0})

    resp = await store.handle_deck_store(_GetReq())
    body = _body(resp)
    assert resp.status == 200
    assert "capabilities" not in body
    assert "capability_errors" not in body


async def test_capabilities_flag_adds_the_index(monkeypatch):
    """?capabilities=1 folds the SAME items — no second store sweep."""
    import navig.hub as hub
    import navig.hub.capabilities as caps_mod

    sweeps = []

    def _collect(**kwargs):
        sweeps.append(1)
        return []

    monkeypatch.setattr(hub, "collect_store", _collect)
    monkeypatch.setattr(hub, "store_status", lambda items: {"total": 0})
    monkeypatch.setattr(
        caps_mod, "collect_capabilities", lambda items=None, **kw: ([_Cap()], [])
    )

    req = _GetReq()
    req.query = {"capabilities": "1"}
    resp = await store.handle_deck_store(req)
    body = _body(resp)
    assert resp.status == 200
    assert body["capabilities"] == [{"id": "perplexity", "backends": []}]
    assert body["capability_errors"] == []
    assert body["api_version"] == store.STORE_API_VERSION
    assert len(sweeps) == 1, "the fold must reuse the collected items, not re-sweep"
