"""``navig discord mod|role|bot profile|settings|setup`` against the fake server."""

from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

import navig.discord.api as api_mod
import navig.discord.config as cfg_mod
from navig.commands.discord import discord_app
from navig.discord import modlog
from navig.discord import settings as settings_mod

from .fake_discord import ADMIN, MEMBER, FakeDiscord, G

runner = CliRunner()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakeDiscord()
    tokens = {"bot": "SECRET-bot-token-xyz"}
    monkeypatch.setattr(cfg_mod, "get_token", lambda identity: tokens.get(identity, ""))
    monkeypatch.setattr(cfg_mod, "have_token", lambda identity: bool(tokens.get(identity)))
    monkeypatch.setattr(cfg_mod, "set_token", lambda identity, token: tokens.__setitem__(identity, token))
    monkeypatch.setattr(cfg_mod, "vault_unavailable_reason", lambda: None)
    monkeypatch.setattr(modlog, "path", lambda: tmp_path / "modlog.jsonl")

    real = api_mod.DiscordClient

    class Patched(real):  # type: ignore[misc, valid-type]
        def __init__(self, identity, token, **kw):
            kw.setdefault("transport", httpx.MockTransport(fake.handler))
            super().__init__(identity, token, **kw)

    monkeypatch.setattr(api_mod, "DiscordClient", Patched)
    # guilds() for server-name resolution
    orig = fake.handler

    def handler(req):
        import json

        if not req.url.path.endswith("/users/@me/guilds") and req.url.path.endswith("/applications/@me"):
            path = req.url.path.removeprefix("/api/v10")
            fake.calls.append((req.method, path, json.loads(req.content) if req.content else None, None))
        if req.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=[{"id": G, "name": "Lab", "owner": False, "permissions": "8"}])
        if req.url.path.endswith("/oauth2/applications/@me"):
            return httpx.Response(200, json={"id": "77", "name": "navig", "bot_public": True, "flags": 0,
                                             "owner": {"id": "1000"}, "description": ""})
        if req.method == "PATCH" and req.url.path.endswith("/applications/@me"):
            return httpx.Response(200, json={"id": "77"})
        return orig(req)

    fake.handler = handler  # type: ignore[method-assign]
    fake.tokens = tokens  # type: ignore[attr-defined]
    return fake


def _run(*args):
    return runner.invoke(discord_app, list(args), env={"COLUMNS": "160"})


def test_kick_needs_confirmation_when_not_interactive(fake):
    res = _run("mod", "kick", "Lab", MEMBER)
    assert res.exit_code == 1 and "--yes" in res.output
    assert fake.writes == []


def test_kick_with_yes(fake):
    res = _run("mod", "kick", "Lab", MEMBER, "--yes", "-r", "spam")
    assert res.exit_code == 0, res.output
    assert ("DELETE", f"/guilds/{G}/members/{MEMBER}") in [(w[0], w[1]) for w in fake.writes]


def test_refusal_exits_1_with_the_reason(fake):
    res = _run("mod", "kick", "Lab", ADMIN, "--yes")
    assert res.exit_code == 1
    assert "Refused" in res.output and "highest role" in res.output
    assert fake.writes == []


def test_timeout_parses_duration_and_rejects_garbage(fake):
    assert _run("mod", "timeout", "Lab", MEMBER, "10m").exit_code == 0
    bad = _run("mod", "timeout", "Lab", MEMBER, "forever")
    assert bad.exit_code == 1 and "not a duration" in bad.output


def test_ban_delete_window_is_bounded(fake):
    res = _run("mod", "ban", "Lab", MEMBER, "--delete", "8d", "--yes")
    assert res.exit_code == 1 and "maximum of 7 days" in res.output
    assert fake.writes == []


def test_mod_log_shows_what_happened(fake):
    _run("mod", "kick", "Lab", MEMBER, "--yes")
    res = _run("mod", "log", "--json")
    assert res.exit_code == 0 and '"action": "kick"' in res.output


def test_role_create_with_named_permissions(fake):
    res = _run("role", "create", "Lab", "Helpers", "-p", "kick_members", "--color", "#00ff00")
    assert res.exit_code == 0, res.output
    post = next(w for w in fake.writes if w[0] == "POST")
    assert post[2]["permissions"] == "2" and post[2]["color"] == 0x00FF00


def test_role_edit_with_nothing_fails_without_requests(fake):
    res = _run("role", "edit", "Lab", "Member")
    assert res.exit_code == 1 and fake.writes == []


def test_settings_table_renders(fake, monkeypatch):
    monkeypatch.setattr(settings_mod, "_cfg", lambda: type("C", (), {"get": lambda self, k, d=None: d})())
    res = _run("settings")
    assert res.exit_code == 0 and "discord.owners" in res.output and "empty = nobody" in res.output


def test_setup_non_interactive_reports_public_bot_and_sets_owner(fake, monkeypatch):
    written: dict[str, object] = {}
    monkeypatch.setattr(settings_mod, "set_value", lambda key, value: written.__setitem__(key, value))
    monkeypatch.setattr(settings_mod, "load", lambda: settings_mod.DiscordSettings(
        owners=frozenset(str(o) for o in written.get("owners", []))))
    import navig.commands.discord as cmd

    monkeypatch.setattr(cmd, "_discord_py_installed", lambda: True)
    res = _run("setup", "--owner", "1000", "--no-open")
    assert res.exit_code == 0, res.output
    assert written["owners"] == ["1000"] and written["enabled"] is True
    assert "anyone can add it" in res.output  # bot_public warning
    patch = [w for w in fake.writes if w[1] == "/applications/@me"]
    assert patch and patch[0][2] == {"flags": 1 << 15}  # members intent switched on
    assert "SECRET-bot-token-xyz" not in res.output
