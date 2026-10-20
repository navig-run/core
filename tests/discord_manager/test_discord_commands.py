"""``navig discord`` commands against a fake Discord and an in-memory token store."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

import navig.discord.api as api_mod
import navig.discord.config as cfg_mod
from navig.commands.discord import discord_app

runner = CliRunner()

GUILDS_ACCOUNT = [
    {"id": "1", "name": "Python", "owner": False, "permissions": "0",
     "approximate_member_count": 400000, "approximate_presence_count": 30000},
    {"id": "2", "name": "My Lab", "owner": True, "permissions": "8",
     "approximate_member_count": 12, "approximate_presence_count": 3},
]
GUILDS_BOT = [{"id": "2", "name": "My Lab", "owner": False, "permissions": "8"}]


class FakeDiscord:
    """Routes requests by (identity, method, path); records every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str, bytes]] = []
        self.overrides: dict[tuple[str, str], httpx.Response] = {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("Authorization", "")
        identity = "bot" if auth.startswith("Bot ") else "account"
        path = req.url.path.removeprefix("/api/v10")
        self.requests.append((identity, req.method, path, req.content))
        if (req.method, path) in self.overrides:
            return self.overrides[(req.method, path)]
        if path == "/users/@me":
            if auth in ("Bot bad", "bad"):
                return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
            if identity == "bot" or auth == "looks-like-bot":
                return httpx.Response(200, json={"id": "900", "username": "navigbot", "bot": True})
            return httpx.Response(200, json={"id": "100", "username": "owner", "global_name": "Owner"})
        if path == "/users/@me/guilds":
            return httpx.Response(200, json=GUILDS_BOT if identity == "bot" else GUILDS_ACCOUNT)
        if req.method == "DELETE" and path.startswith("/users/@me/guilds/"):
            return httpx.Response(204)
        if path == "/guilds/2/channels" and req.method == "GET":
            return httpx.Response(200, json=[
                {"id": "50", "name": "Ops", "type": 4, "position": 0},
                {"id": "51", "name": "alerts", "type": 0, "position": 0, "parent_id": "50", "topic": "pager"},
            ])
        if path == "/guilds/2/channels" and req.method == "POST":
            body = json.loads(req.content)
            return httpx.Response(201, json={"id": "60", **body})
        if path == "/guilds/2/members":
            return httpx.Response(200, json=[
                {"user": {"id": "100", "username": "owner"}, "roles": ["7"], "joined_at": "2024-01-02T00:00:00"},
            ])
        return httpx.Response(404, json={"message": "Unknown", "code": 0})


@pytest.fixture
def discord(monkeypatch):
    fake = FakeDiscord()
    tokens: dict[str, str] = {}

    monkeypatch.setattr(cfg_mod, "get_token", lambda identity: tokens.get(identity, ""))
    monkeypatch.setattr(cfg_mod, "have_token", lambda identity: bool(tokens.get(identity)))
    monkeypatch.setattr(cfg_mod, "set_token", lambda identity, token: tokens.__setitem__(identity, token))
    monkeypatch.setattr(cfg_mod, "clear_token", lambda identity: tokens.pop(identity, None) is not None)
    monkeypatch.setattr(cfg_mod, "vault_unavailable_reason", lambda: None)

    real = api_mod.DiscordClient

    class Patched(real):  # type: ignore[misc, valid-type]
        def __init__(self, identity, token, **kw):
            kw.setdefault("transport", httpx.MockTransport(fake.handler))
            super().__init__(identity, token, **kw)

    monkeypatch.setattr(api_mod, "DiscordClient", Patched)
    fake.tokens = tokens  # type: ignore[attr-defined]
    return fake


def _run(*args: str):
    return runner.invoke(discord_app, list(args), env={"COLUMNS": "160"})


def test_nothing_configured_fails_with_setup_hint(discord):
    res = _run("servers")
    assert res.exit_code == 1
    assert "navig discord bot set" in res.output


def test_servers_as_account_marks_where_the_bot_is(discord):
    discord.tokens.update(account="acct", bot="b")
    res = _run("servers", "--json")
    assert res.exit_code == 0, res.output
    rows = {r["id"]: r for r in json.loads(res.output)}
    assert rows["1"]["bot"] is False and rows["1"]["admin"] is False
    assert rows["2"]["bot"] is True and rows["2"]["owner"] is True


def test_servers_table_renders(discord):
    discord.tokens.update(account="acct")
    res = _run("servers")
    assert res.exit_code == 0, res.output
    assert "Python" in res.output and "400,000" in res.output and "2 servers as account" in res.output


def test_leave_without_yes_refuses_non_interactively_and_sends_nothing(discord):
    discord.tokens.update(account="acct")
    res = _run("leave", "Python")
    assert res.exit_code == 1
    assert "--yes" in res.output
    assert not [r for r in discord.requests if r[1] == "DELETE"]


def test_leave_with_yes_leaves(discord):
    discord.tokens.update(account="acct")
    res = _run("leave", "python", "--yes")
    assert res.exit_code == 0, res.output
    assert ("account", "DELETE", "/users/@me/guilds/1", b"") in discord.requests


def test_owner_cannot_leave_and_no_request_is_sent(discord):
    discord.tokens.update(account="acct")
    res = _run("leave", "My Lab", "--yes")
    assert res.exit_code == 1
    assert "owner cannot leave" in res.output
    assert not [r for r in discord.requests if r[1] == "DELETE"]


def test_members_never_uses_the_account_token(discord):
    discord.tokens.update(account="acct")
    res = _run("members", "My Lab")
    assert res.exit_code == 1
    assert "No bot token" in res.output
    assert not any("/members" in r[2] for r in discord.requests)


def test_members_lists_with_the_bot(discord):
    discord.tokens.update(bot="b")
    res = _run("members", "My Lab", "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[0]["user"]["id"] == "100"


def test_members_missing_intent_gets_an_actionable_hint(discord):
    discord.tokens.update(bot="b")
    discord.overrides[("GET", "/guilds/2/members")] = httpx.Response(
        403, json={"message": "Missing Access", "code": 50001})
    res = _run("members", "2")
    assert res.exit_code == 1
    assert "Server Members" in res.output


def test_login_requires_explicit_opt_in_when_not_interactive(discord):
    res = _run("login", "--token", "acct")
    assert res.exit_code == 1
    assert "--accept-risk" in res.output
    assert discord.tokens == {}


def test_login_validates_then_stores(discord):
    res = _run("login", "--token", "acct", "--accept-risk")
    assert res.exit_code == 0, res.output
    assert discord.tokens == {"account": "acct"}
    assert "Owner" in res.output


def test_login_refuses_a_rejected_token(discord):
    res = _run("login", "--token", "bad", "--accept-risk")
    assert res.exit_code == 1
    assert discord.tokens == {}


def test_login_refuses_a_bot_token(discord):
    res = _run("login", "--token", "looks-like-bot", "--accept-risk")
    assert res.exit_code == 1
    assert "bot" in res.output
    assert discord.tokens == {}


def test_status_exits_nonzero_when_a_stored_token_is_rejected(discord):
    discord.tokens.update(bot="bad")
    res = _run("status", "--json")
    assert res.exit_code == 1
    rows = {r["identity"]: r for r in json.loads(res.output)}
    assert rows["bot"]["ok"] is False
    assert rows["account"]["configured"] is False


def test_channel_create_resolves_category_by_name(discord):
    discord.tokens.update(bot="b")
    res = _run("channel", "create", "My Lab", "deploys", "--category", "ops", "--topic", "ship it")
    assert res.exit_code == 0, res.output
    posted = [r for r in discord.requests if r[1] == "POST"]
    assert json.loads(posted[0][3]) == {"name": "deploys", "type": 0, "parent_id": "50", "topic": "ship it"}


def test_channel_delete_needs_confirmation(discord):
    discord.tokens.update(bot="b")
    res = _run("channel", "delete", "51")
    assert res.exit_code == 1
    assert not [r for r in discord.requests if r[1] == "DELETE"]


def test_channel_edit_with_nothing_to_change_fails(discord):
    discord.tokens.update(bot="b")
    res = _run("channel", "edit", "51")
    assert res.exit_code == 1
    assert discord.requests == []
