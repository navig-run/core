"""Universal presets surfaces: deck routes and the `navig presets` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from navig.presets import catalog
from navig.store import prompt_library


@pytest.fixture
def cat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    prompt_library.reset_prompt_store()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "noir.preset.yaml").write_text(
        yaml.safe_dump({"id": "image/style/noir", "name": "Noir", "tool": "image",
                        "params": {"mood": "bleak", "aspect": "16:9"}}),
        encoding="utf-8",
    )
    (builtin / "lit.preset.yaml").write_text(
        yaml.safe_dump({"id": "text/style/lit", "name": "Literary", "tool": "text",
                        "instructions": "Write with restraint."}),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "preset_roots", lambda: [("builtin", builtin)])
    monkeypatch.setattr(catalog, "context_root", lambda: None)
    yield catalog
    prompt_library.reset_prompt_store()


class _Req:
    def __init__(self, body=None, query=None):
        self._body = body
        self.query = query or {}
        self.content_length = len(json.dumps(body)) if isinstance(body, (dict, list)) else 0

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _b(resp):
    return resp.status, json.loads(resp.body.decode())


async def test_routes_crud_and_resolve(cat):
    from navig.gateway.deck.routes import presets as r

    st, body = _b(await r.handle_list(_Req(query={"tool": "image"})))
    assert st == 200 and [p["id"] for p in body["data"]] == ["image/style/noir"]

    st, body = _b(await r.handle_create(_Req({"name": "Warm", "tool": "image", "params": {"mood": "warm"}})))
    assert st == 201 and body["data"]["id"] == "user/warm"

    st, body = _b(await r.handle_create(_Req({"name": "Bad", "tool": "image", "params": {"aspect": "7:5"}})))
    assert st == 400 and "aspect" in body["error"]

    st, body = _b(await r.handle_update(_Req({"description": "edited"}, {"id": "image/style/noir"})))
    assert st == 200 and body["data"]["description"] == "edited" and body["data"]["overrides"] == 1

    st, body = _b(await r.handle_reset(_Req({"id": "image/style/noir"})))
    assert st == 200 and body["data"]["reset"] is True and body["data"]["preset"]["description"] == ""

    st, body = _b(await r.handle_delete(_Req(query={"id": "image/style/noir"})))
    assert st == 400  # a builtin can be reset, not deleted

    st, body = _b(await r.handle_default(_Req({"tool": "image", "id": "user/warm"})))
    assert body["data"] == {"tool": "image", "id": "user/warm"}

    st, body = _b(await r.handle_resolve(_Req({"tool": "image", "ids": ["image/style/noir"],
                                                "task": "A lighthouse", "provider": "openai_gpt_image"})))
    assert st == 200
    data = body["data"]
    assert data["prompt"].startswith("A lighthouse") and data["provider_request"]["size"] == "1536x1024"

    st, body = _b(await r.handle_get(_Req(query={"id": "nope"})))
    assert st == 404


async def test_routes_export_import_trust(cat):
    from navig.gateway.deck.routes import presets as r

    st, body = _b(await r.handle_export(_Req(query={"id": "text/style/lit"})))
    doc = body["data"]["document"]
    assert doc["kind"] == catalog.EXPORT_KIND
    st, body = _b(await r.handle_import(_Req({"document": doc})))
    assert st == 200 and body["data"]["trusted"] is False
    st, body = _b(await r.handle_trust(_Req({"id": body["data"]["id"]})))
    assert body["data"]["trusted"] is True
    st, body = _b(await r.handle_import(_Req({"document": {"kind": "nope"}})))
    assert st == 400


async def test_malformed_json_is_a_400(cat):
    from navig.gateway.deck.routes import presets as r

    st, _ = _b(await r.handle_create(_Req(ValueError("bad json"))))
    assert st == 400


def test_routes_are_registered_once():
    from aiohttp import web

    from navig.gateway.deck.routes import presets as r

    app = web.Application()
    r.register(app)
    paths = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("POST", "/api/deck/presets/resolve") in paths
    assert ("GET", "/api/deck/presets") in paths


def test_cli_list_show_resolve_new_export_import(cat, tmp_path):
    from navig.commands.presets import presets_app

    runner = CliRunner()
    res = runner.invoke(presets_app, ["list", "--json"])
    assert res.exit_code == 0, res.output
    assert {p["id"] for p in json.loads(res.output)} == {"image/style/noir", "text/style/lit"}

    res = runner.invoke(presets_app, ["show", "nope"])
    assert res.exit_code == 1

    res = runner.invoke(presets_app, ["resolve", "text/style/lit", "--tool", "text", "--task", "Go.", "--json"])
    assert res.exit_code == 0, res.output
    assert "Write with restraint." in json.loads(res.output)["system_text"]

    res = runner.invoke(presets_app, ["resolve", "image/style/noir", "--tool", "text", "--task", "x"])
    assert res.exit_code == 1  # wrong tool is an error, not a silent no-op

    res = runner.invoke(presets_app, ["new", "--name", "Mine", "--tool", "text", "-i", "Be brief.",
                                      "--set", "max_words=200", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["params"] == {"max_words": 200}

    res = runner.invoke(presets_app, ["new", "--name", "Bad", "--tool", "text", "--set", "aspect=1:1"])
    assert res.exit_code == 1

    out = tmp_path / "mine.json"
    res = runner.invoke(presets_app, ["export", "user/mine", "-o", str(out)])
    assert res.exit_code == 0 and out.is_file()
    res = runner.invoke(presets_app, ["import", str(out)])
    assert res.exit_code == 0, res.output
    assert any(p.source == "imported" for p in catalog.list_presets(tool="text"))

    res = runner.invoke(presets_app, ["rm", "user/mine", "--yes"])
    assert res.exit_code == 0
    res = runner.invoke(presets_app, ["rm", "text/style/lit", "--yes"])
    assert res.exit_code == 1
