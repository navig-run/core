"""Regression: /cloud/status coerces a stored string ``cloud.enabled`` (not raw ``bool()``).

``navig config set cloud.enabled false`` stores the **string** ``"false"``, and
``bool("false")`` is ``True`` -- so every reader that did ``bool(cfg.get("cloud.enabled"))``
reported cloud **ENABLED** for a user who had explicitly turned it off. All ``cloud.enabled``
reads (CLI ``cloud status`` / ``init`` advertise / ``status`` / the Telegram broker-bind gate /
this deck route / onboarding brain-detect) now go through the canonical
``navig.core.coerce.coerce_bool``. This pins ``handle_deck_cloud_status`` as the representative
reader; a revert to raw ``bool()`` fails ``test_stored_string_false_reads_disabled``.

Same footgun family as ``test_ops_toggle.py`` (deck ops toggle) -- ``bool("false")`` truthiness.
"""

from __future__ import annotations

import json as _json

import pytest

pytestmark = pytest.mark.integration

# The handler returns web.json_response(...); skip cleanly if aiohttp is unavailable.
pytest.importorskip("aiohttp.web")  # aiohttp.web is a lazy submodule -- import it directly

_UNSET = object()


class _FakeCfg:
    """Minimal Config stand-in.

    Only ``cloud.enabled`` (when set) is meaningful; ``cloud.public_url`` is fixed
    non-empty so the handler skips its license/relay-gate eval -- a real, network-ish
    read that is irrelevant to the ``enabled`` field under test. Every other key falls
    through to the caller's ``.get`` default.
    """

    def __init__(self, enabled_value=_UNSET):
        self._values: dict[str, object] = {"cloud.public_url": "https://relay.example.test"}
        if enabled_value is not _UNSET:
            self._values["cloud.enabled"] = enabled_value

    def get(self, key, default=None):
        return self._values.get(key, default)


class _Req:
    """aiohttp request stand-in: only ``request.app.get(...)`` is exercised (gateway lookup)."""

    def __init__(self):
        self.app: dict = {}  # .get("gateway") -> None => no live-manager override of `enabled`


def _payload(resp):
    return _json.loads(resp.body.decode())


@pytest.fixture()
def patch_config(monkeypatch):
    """Return an installer that points the deck cloud route at a _FakeCfg."""

    def _install(enabled_value=_UNSET):
        from navig.gateway.deck.routes import cloud as cloud_routes

        monkeypatch.setattr(cloud_routes, "_config", lambda: _FakeCfg(enabled_value))

    return _install


async def test_stored_string_false_reads_disabled(patch_config):
    """The bug: a stored ``"false"`` string must report ``enabled=False`` (was True via bool())."""
    from navig.gateway.deck.routes.cloud import handle_deck_cloud_status

    patch_config("false")
    assert _payload(await handle_deck_cloud_status(_Req()))["enabled"] is False


async def test_stored_string_true_reads_enabled(patch_config):
    from navig.gateway.deck.routes.cloud import handle_deck_cloud_status

    patch_config("true")
    assert _payload(await handle_deck_cloud_status(_Req()))["enabled"] is True


async def test_real_bool_false_reads_disabled(patch_config):
    """coerce_bool is a strict superset of bool -- a real ``False`` still reads disabled."""
    from navig.gateway.deck.routes.cloud import handle_deck_cloud_status

    patch_config(False)
    assert _payload(await handle_deck_cloud_status(_Req()))["enabled"] is False


async def test_missing_key_keeps_default_on(patch_config):
    """Absent ``cloud.enabled`` keeps the intentional default-ON (get default True)."""
    from navig.gateway.deck.routes.cloud import handle_deck_cloud_status

    patch_config()  # cloud.enabled not set at all
    assert _payload(await handle_deck_cloud_status(_Req()))["enabled"] is True
