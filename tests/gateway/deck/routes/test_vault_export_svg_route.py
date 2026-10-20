"""POST /api/deck/vault/export-svg — the desktop app's "Export" button.

Every other vault route returns masked metadata; this one returns decryptable secrets and
the passphrase for them, so the property that matters most is that it answers ONLY a
request from this computer (not one relayed through Lighthouse, the tunnel or the Mini App).
Runs against a real ``Vault(tmp_path)`` patched in — never the developer's vault.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes.vault import handle_deck_vault_export_svg

pytestmark = pytest.mark.integration

ROUTE = "/api/deck/vault/export-svg"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    from navig_vault import svg
    from navig_vault.core import Vault

    monkeypatch.setattr(svg, "ITERATIONS", 1000)
    v = Vault(tmp_path / "v")
    v.add("github", "token", {"token": "ghp_route_secret"})
    v.put("note", b"plain note")
    with patch("navig.gateway.deck.routes.vault._get_vault", return_value=v):
        yield v


def _app() -> web.Application:
    app = web.Application()
    app.router.add_post(ROUTE, handle_deck_vault_export_svg)
    return app


async def _post(body, headers=None):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(ROUTE, json=body, headers=headers or {})
        return resp.status, await resp.json()


async def test_a_local_request_gets_an_svg_that_opens_with_its_passphrase(vault):
    from navig_vault import svg

    status, body = await _post({"all": True})
    assert status == 200, body
    [f] = body["files"]
    assert f["name"].startswith("vault-") and f["name"].endswith(".svg")
    assert "ghp_route_secret" not in f["svg"] and "github" not in f["svg"]
    labels = sorted(e["label"] for e in svg.open_svg(f["svg"], f["passphrase"]).items)
    assert labels == sorted(f["labels"]) == ["github", "note"]


async def test_selected_ids_and_each_give_one_file_per_item(vault):
    ids = [i.id for i in vault.list()]
    status, body = await _post({"ids": ids, "each": True})
    assert status == 200, body
    assert len(body["files"]) == 2 and len({f["passphrase"] for f in body["files"]}) == 2


@pytest.mark.parametrize("headers", [
    {"X-Navig-Via": "lighthouse"},  # relayed in from the edge
    {"CF-Ray": "8a1b2c3d4e5f-AMS"},  # through the tunnel
    {"X-Forwarded-For": "203.0.113.7"},  # a non-local client behind a proxy
])
async def test_anything_not_from_this_computer_is_refused(vault, headers):
    status, body = await _post({"all": True}, headers)
    assert status == 403 and "files" not in body


@pytest.mark.parametrize("payload", [
    {},  # neither ids nor all
    {"ids": ["x"], "all": True},  # both
    {"ids": "not-a-list"},
    {"ids": [""]},
    {"all": "true"},  # a string is not true
    ["all"],
])
async def test_an_ambiguous_request_is_a_400(vault, payload):
    status, _ = await _post(payload)
    assert status == 400


async def test_an_unknown_id_is_a_404_not_a_partial_export(vault):
    status, body = await _post({"ids": ["no-such-id"]})
    assert status == 404 and "files" not in body


async def test_an_older_navig_vault_gets_an_update_hint(vault, monkeypatch):
    """The four verbs import navig_vault.svg inside a try — a clean 501, never a traceback.

    The floor is nudged forward to match (tests/packaging/test_navig_vault_floor.py), but
    an environment that pins an older navig-vault still has to get an instruction rather
    than an unhandled ImportError from a gateway route.
    """
    import sys as _sys
    import types

    # Two things have to go: the package (a module with no __path__, so
    # `from navig_vault import svg` cannot fall back to importing a submodule — the shape
    # of an install without svg.py) and the submodules themselves, which a previous test
    # already put in sys.modules, where the import machinery finds them without ever
    # consulting the parent.
    monkeypatch.setitem(_sys.modules, "navig_vault", types.ModuleType("navig_vault"))
    for sub in ("svg", "svg_export", "transfer"):
        monkeypatch.delitem(_sys.modules, f"navig_vault.{sub}", raising=False)
    status, body = await _post({"all": True})
    assert status == 501, body
    assert "pip install -U navig-vault" in body["error"]
