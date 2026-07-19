"""Regression: the deck ops toggle honors per-toggle defaults + coerces string values.

The snapshot (handle_deck_ops) and the flip (handle_deck_ops_toggle) once used DIFFERENT
defaults for the same keys — the snapshot defaulted auto_continue / auto_dispatch to False (OFF)
while the flip defaulted them to True. So an unset auto_continue showed OFF but the first "turn on"
click computed ``not True → False`` and nothing changed (it took two clicks). Both now read the one
`_TOGGLE_DEFAULTS` source, and a raw-string ``value`` ('false'/'0'/'off') is honored via the
canonical ``navig.core.coerce.coerce_bool`` rather than being silently truthy (``bool("false")`` is
``True``).
"""

from __future__ import annotations

import json as _json

import pytest

pytestmark = pytest.mark.integration


class _Req:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _payload(resp):
    return _json.loads(resp.body.decode())


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """A real ConfigManager isolated to a tmp config dir (no global patching side effects)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    from navig.config import get_config_manager, reset_config_manager

    reset_config_manager()
    yield get_config_manager()
    reset_config_manager()


async def test_flip_unset_auto_continue_turns_on(isolated_cfg):
    """The two-click bug: unset auto_continue (OFF) must turn ON on the FIRST flip."""
    from navig.gateway.deck.routes.ops import handle_deck_ops_toggle

    resp = await handle_deck_ops_toggle(_Req({"toggle": "auto_continue"}))  # value omitted = flip
    p = _payload(resp)
    assert p["ok"] is True
    assert p["value"] is True  # was False under the old True-default (stayed OFF)
    assert (isolated_cfg.get("ai") or {}).get("auto_continue") is True  # persisted


async def test_flip_unset_smart_ai_turns_off(isolated_cfg):
    """smart_ai defaults ON, so the first flip from unset turns it OFF."""
    from navig.gateway.deck.routes.ops import handle_deck_ops_toggle

    resp = await handle_deck_ops_toggle(_Req({"toggle": "smart_ai"}))
    assert _payload(resp)["value"] is False


async def test_snapshot_and_flip_defaults_agree(isolated_cfg):
    """Every stored toggle: the snapshot default and the flip-from-unset result are consistent."""
    from navig.gateway.deck.routes.ops import handle_deck_ops, handle_deck_ops_toggle

    snap = _payload(await handle_deck_ops(_Req({})))["toggles"]
    assert snap["auto_continue"] is False
    assert snap["auto_dispatch"] is False
    assert snap["smart_ai"] is True

    for key in ("auto_continue", "auto_dispatch", "smart_ai"):
        r = _payload(await handle_deck_ops_toggle(_Req({"toggle": key})))
        assert r["value"] is (not snap[key]), f"{key} flip disagreed with snapshot default"


async def test_string_value_false_is_honored(isolated_cfg):
    """A client sending the raw string 'false' must set OFF, not be silently truthy."""
    from navig.gateway.deck.routes.ops import handle_deck_ops_toggle

    r = _payload(await handle_deck_ops_toggle(_Req({"toggle": "smart_ai", "value": "false"})))
    assert r["value"] is False


async def test_explicit_value_sets_directly(isolated_cfg):
    from navig.gateway.deck.routes.ops import handle_deck_ops_toggle

    r = _payload(await handle_deck_ops_toggle(_Req({"toggle": "auto_dispatch", "value": True})))
    assert r["value"] is True
    assert (isolated_cfg.get("ai") or {}).get("auto_dispatch") is True


async def test_unknown_toggle_rejected(isolated_cfg):
    from navig.gateway.deck.routes.ops import handle_deck_ops_toggle

    resp = await handle_deck_ops_toggle(_Req({"toggle": "bogus"}))
    assert resp.status == 400
    p = _payload(resp)
    assert p["ok"] is False
    assert "bogus" in p["error"]
