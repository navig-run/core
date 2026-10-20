"""Contract tests for the deck's Discord routes (``/api/deck/discord/*``).

The routes are the deck/OS face of ``navig.discord``: they must

* answer 409 with the setup hint when no token is stored, never a 500;
* list servers with the bot-presence flag the UI draws as ●/○;
* run every moderation write through ``moderation.member_action`` (policy first,
  then Discord, then the modlog) and map a policy refusal to 403 + the reason;
* refuse any mutating POST without ``"confirm": true`` — before Discord is called;
* never put a token in any response body.

Discord itself is an ``httpx.MockTransport`` fake; tokens come from an in-memory
store patched over ``navig.discord.config``.
"""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.discord.api as api_mod
import navig.discord.config as cfg_mod
from navig.discord import modlog, policy
from navig.gateway.deck.routes import discord as routes

pytestmark = pytest.mark.integration

BOT_TOKEN = "BOT.SECRET-token-value-123"
USER_TOKEN = "USER.SECRET-token-value-456"

G = "2"
OWNER, BOT, MOD, MEMBER = "1000", "900", "200", "300"
MOD_PERMS = policy.KICK_MEMBERS | policy.BAN_MEMBERS | policy.MODERATE_MEMBERS | policy.MANAGE_ROLES
ROLES = [
    {"id": G, "name": "@everyone", "position": 0, "permissions": "0", "managed": False},
    {"id": "10", "name": "Admin", "position": 5, "permissions": str(policy.ADMINISTRATOR), "managed": False},
    {"id": "13", "name": "NAVIG", "position": 4, "permissions": str(MOD_PERMS), "managed": True},
    {"id": "11", "name": "Mod", "position": 3, "permissions": str(MOD_PERMS), "managed": False},
    {"id": "12", "name": "Member", "position": 1, "permissions": "0", "managed": False},
]
MEMBERS = {OWNER: [], BOT: ["13"], MOD: ["11"], MEMBER: ["12"], "400": ["10"]}
GUILDS_ACCOUNT = [
    {"id": "1", "name": "Python", "owner": False, "permissions": "0",
     "approximate_member_count": 400000, "approximate_presence_count": 30000},
    {"id": G, "name": "My Lab", "owner": True, "permissions": "8",
     "approximate_member_count": 12, "approximate_presence_count": 3},
]
GUILDS_BOT = [{"id": G, "name": "My Lab", "owner": False, "permissions": "8"}]


class FakeDiscord:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, dict | None]] = []

    @property
    def writes(self) -> list[tuple[str, str, str, dict | None]]:
        return [c for c in self.calls if c[1] != "GET"]

    def handler(self, req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("Authorization", "")
        identity = "bot" if auth.startswith("Bot ") else "account"
        path = req.url.path.removeprefix("/api/v10")
        body = json.loads(req.content) if req.content else None
        self.calls.append((identity, req.method, path, body))
        if req.method == "GET":
            if path == "/users/@me":
                if identity == "bot":
                    return httpx.Response(200, json={"id": BOT, "username": "navigbot", "bot": True})
                return httpx.Response(200, json={"id": OWNER, "username": "owner", "global_name": "Owner"})
            if path == "/users/@me/guilds":
                return httpx.Response(200, json=GUILDS_BOT if identity == "bot" else GUILDS_ACCOUNT)
            if path == f"/guilds/{G}":
                return httpx.Response(200, json={"id": G, "name": "My Lab", "owner_id": OWNER,
                                                 "approximate_member_count": 12})
            if path == f"/guilds/{G}/roles":
                return httpx.Response(200, json=ROLES)
            if path == f"/guilds/{G}/channels":
                return httpx.Response(200, json=[
                    {"id": "50", "name": "Ops", "type": 4, "position": 0},
                    {"id": "51", "name": "alerts", "type": 0, "position": 1, "parent_id": "50"},
                ])
            if path == f"/guilds/{G}/members":
                return httpx.Response(200, json=[{"user": {"id": MEMBER, "username": "m"}, "roles": ["12"]}])
            if path.startswith(f"/guilds/{G}/members/"):
                uid = path.rsplit("/", 1)[1]
                if uid not in MEMBERS:
                    return httpx.Response(404, json={"message": "Unknown Member", "code": 10007})
                return httpx.Response(200, json={"user": {"id": uid}, "roles": MEMBERS[uid]})
            if path == f"/guilds/{G}/bans":
                return httpx.Response(200, json=[{"user": {"id": "777", "username": "spam"}, "reason": "spam"}])
            if path.startswith("/guilds/"):
                return httpx.Response(404, json={"message": "Unknown Guild", "code": 10004})
            return httpx.Response(404, json={"message": "Unknown", "code": 0})
        if req.method == "PATCH" and path.startswith(f"/guilds/{G}/members/"):
            return httpx.Response(200, json={"user": {"id": path.rsplit("/", 1)[1]}})
        return httpx.Response(204)


@pytest.fixture
def env(monkeypatch, tmp_path):
    fake = FakeDiscord()
    tokens: dict[str, str] = {}
    monkeypatch.setattr(cfg_mod, "get_token", lambda identity: tokens.get(identity, ""))
    monkeypatch.setattr(cfg_mod, "have_token", lambda identity: bool(tokens.get(identity)))
    monkeypatch.setattr(cfg_mod, "vault_unavailable_reason", lambda: None)
    monkeypatch.setattr(modlog, "path", lambda: tmp_path / "discord" / "modlog.jsonl")

    real = api_mod.DiscordClient

    class Patched(real):  # type: ignore[misc, valid-type]
        def __init__(self, identity, token, **kw):
            kw.setdefault("transport", httpx.MockTransport(fake.handler))
            kw.setdefault("sleep", lambda _s: None)
            super().__init__(identity, token, **kw)

    monkeypatch.setattr(api_mod, "DiscordClient", Patched)
    return fake, tokens


@pytest.fixture
async def client(env):
    app = web.Application()
    routes.register(app)
    async with TestClient(TestServer(app)) as c:
        yield c


async def _json(resp) -> tuple[int, dict, str]:
    text = await resp.text()
    return resp.status, json.loads(text), text


def _no_secrets(text: str) -> None:
    assert BOT_TOKEN not in text
    assert USER_TOKEN not in text


# ── not configured ───────────────────────────────────────────────────────────


async def test_servers_without_any_token_is_409_with_setup_hint(client, env):
    status, body, text = await _json(await client.get("/api/deck/discord/servers"))
    assert status == 409
    assert body["ok"] is False and body["kind"] == "not_configured"
    assert "navig discord setup" in body["hint"]


async def test_members_without_bot_token_is_409(client, env):
    _, tokens = env
    tokens["account"] = USER_TOKEN
    status, body, text = await _json(await client.get(f"/api/deck/discord/servers/{G}/members"))
    assert status == 409 and body["kind"] == "not_configured"
    _no_secrets(text)


async def test_status_reports_both_identities_and_never_the_tokens(client, env):
    _, tokens = env
    tokens["bot"] = BOT_TOKEN
    status, body, text = await _json(await client.get("/api/deck/discord/status"))
    assert status == 200
    data = body["data"]
    assert data["bot"] == {**data["bot"], "configured": True, "ok": True, "user": "navigbot", "servers": 1}
    assert data["account"]["configured"] is False and "navig discord login" in data["account"]["hint"]
    assert data["settings"]["moderation_enabled"] is True
    _no_secrets(text)


# ── reads ────────────────────────────────────────────────────────────────────


async def test_servers_lists_account_guilds_with_bot_presence(client, env):
    _, tokens = env
    tokens.update(bot=BOT_TOKEN, account=USER_TOKEN)
    status, body, text = await _json(await client.get("/api/deck/discord/servers"))
    assert status == 200
    data = body["data"]
    assert data["identity"] == "account"
    rows = {r["id"]: r for r in data["servers"]}
    assert [r["name"] for r in data["servers"]] == ["My Lab", "Python"]  # sorted by name
    assert rows[G] == {"id": G, "name": "My Lab", "owner": True, "admin": True,
                       "members": 12, "online": 3, "bot": True}
    assert rows["1"]["bot"] is False
    _no_secrets(text)


async def test_server_detail_returns_channels_and_roles(client, env):
    _, tokens = env
    tokens["bot"] = BOT_TOKEN
    status, body, _ = await _json(await client.get(f"/api/deck/discord/servers/{G}"))
    assert status == 200
    data = body["data"]
    assert data["bot_present"] is True and data["guild"]["owner_id"] == OWNER
    assert [c["kind"] for c in data["channels"]] == ["category", "text"]
    assert data["roles"][0]["name"] == "Admin" and data["roles"][0]["admin"] is True
    assert data["roles"][-1]["everyone"] is True


async def test_server_detail_falls_back_to_account_when_bot_is_not_there(client, env):
    _, tokens = env
    tokens.update(bot=BOT_TOKEN, account=USER_TOKEN)
    status, body, _ = await _json(await client.get("/api/deck/discord/servers/1"))
    # The fake answers 10004 for guild 1 to both identities — the bot attempt is
    # skipped, the account's answer is the one reported.
    assert status == 502 and body["kind"] == "discord" and body["code"] == 10004
    assert body["status"] == 404 and body["hint"]
    asked = [(c[0], c[2]) for c in env[0].calls if c[2] == "/guilds/1"]
    assert asked == [("bot", "/guilds/1"), ("account", "/guilds/1")]


async def test_non_digit_ids_are_rejected_before_discord(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    status, body, _ = await _json(await client.get("/api/deck/discord/servers/abc/members"))
    assert status == 400 and body["kind"] == "invalid"
    assert fake.calls == []


async def test_bans_and_modlog(client, env):
    _, tokens = env
    tokens["bot"] = BOT_TOKEN
    status, body, _ = await _json(await client.get(f"/api/deck/discord/servers/{G}/bans"))
    assert status == 200 and body["data"]["bans"][0]["id"] == "777"
    status, body, _ = await _json(await client.get(f"/api/deck/discord/modlog?guild_id={G}"))
    assert status == 200 and body["data"]["entries"] == []


# ── moderation ───────────────────────────────────────────────────────────────


async def test_kick_happy_path_goes_through_moderation_and_modlog(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    resp = await client.post(f"/api/deck/discord/servers/{G}/moderate",
                             json={"action": "kick", "user_id": MEMBER, "reason": "spam", "confirm": True})
    status, body, text = await _json(resp)
    assert status == 200 and body["data"]["done"] is True
    assert fake.writes == [("bot", "DELETE", f"/guilds/{G}/members/{MEMBER}", None)]
    entries = modlog.tail(10, G)
    assert entries[0]["action"] == "kick" and entries[0]["surface"] == "deck" and entries[0]["ok"] is True
    _no_secrets(text)


async def test_timeout_parses_duration(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    resp = await client.post(f"/api/deck/discord/servers/{G}/moderate",
                             json={"action": "timeout", "user_id": MEMBER, "duration": "10m", "confirm": True})
    status, _, _ = await _json(resp)
    assert status == 200
    method, path, payload = fake.writes[0][1:]
    assert method == "PATCH" and payload["communication_disabled_until"]


async def test_policy_refusal_is_403_with_the_reason_and_nothing_sent(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    # The server owner is untouchable.
    resp = await client.post(f"/api/deck/discord/servers/{G}/moderate",
                             json={"action": "ban", "user_id": OWNER, "confirm": True})
    status, body, text = await _json(resp)
    assert status == 403 and body["kind"] == "policy"
    assert body["reason"] and body["reason"] == body["error"]
    assert fake.writes == []
    _no_secrets(text)


async def test_missing_confirm_is_400_and_discord_is_never_called(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    for path, payload in (
        (f"/api/deck/discord/servers/{G}/moderate", {"action": "kick", "user_id": MEMBER}),
        (f"/api/deck/discord/servers/{G}/roles", {"op": "delete", "role": "Member"}),
        (f"/api/deck/discord/servers/{G}/leave", {"as": "bot", "confirm": "yes"}),
    ):
        status, body, _ = await _json(await client.post(path, json=payload))
        assert status == 400 and body["kind"] == "confirm_required", path
    assert fake.calls == []


async def test_bad_action_and_bad_duration_are_400(client, env):
    _, tokens = env
    tokens["bot"] = BOT_TOKEN
    url = f"/api/deck/discord/servers/{G}/moderate"
    status, body, _ = await _json(await client.post(url, json={"action": "nuke", "user_id": MEMBER, "confirm": True}))
    assert status == 400 and body["kind"] == "invalid"
    status, body, _ = await _json(await client.post(
        url, json={"action": "timeout", "user_id": MEMBER, "duration": "forever", "confirm": True}))
    assert status == 400 and "duration" in body["error"]


async def test_role_add_and_create(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    url = f"/api/deck/discord/servers/{G}/roles"
    status, _, _ = await _json(await client.post(
        url, json={"op": "add", "role": "Member", "user_id": MOD, "confirm": True}))
    assert status == 200
    assert fake.writes[-1][1:3] == ("PUT", f"/guilds/{G}/members/{MOD}/roles/12")
    status, _, _ = await _json(await client.post(
        url, json={"op": "create", "name": "Helpers", "permissions": "kick_members", "color": "#ff8800",
                   "confirm": True}))
    assert status == 200
    method, path, payload = fake.writes[-1][1:]
    assert (method, path) == ("POST", f"/guilds/{G}/roles")
    assert payload["name"] == "Helpers" and payload["color"] == 0xFF8800


async def test_role_delete_above_the_bot_is_policy_denied(client, env):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    status, body, _ = await _json(await client.post(
        f"/api/deck/discord/servers/{G}/roles", json={"op": "delete", "role": "Admin", "confirm": True}))
    assert status == 403 and body["kind"] == "policy"
    assert fake.writes == []


async def test_account_cannot_moderate(client, env):
    fake, tokens = env
    tokens["account"] = USER_TOKEN
    status, body, text = await _json(await client.post(
        f"/api/deck/discord/servers/{G}/moderate", json={"action": "kick", "user_id": MEMBER, "confirm": True}))
    assert status == 409 and body["kind"] == "not_configured"
    assert fake.writes == []
    _no_secrets(text)


async def test_owner_cannot_leave_their_own_server(client, env):
    fake, tokens = env
    tokens["account"] = USER_TOKEN
    status, body, _ = await _json(await client.post(
        f"/api/deck/discord/servers/{G}/leave", json={"as": "account", "confirm": True}))
    assert status == 400 and "owner cannot leave" in body["error"]
    assert fake.writes == []
    status, body, _ = await _json(await client.post(
        "/api/deck/discord/servers/1/leave", json={"as": "account", "confirm": True}))
    assert status == 200 and body["data"]["left"] is True
    assert fake.writes == [("account", "DELETE", "/users/@me/guilds/1", None)]


async def test_rejected_token_never_echoes_the_token(client, env, monkeypatch):
    fake, tokens = env
    tokens["bot"] = BOT_TOKEN
    original = fake.handler

    def unauthorized(req):
        if req.url.path.endswith("/users/@me"):
            return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
        return original(req)

    monkeypatch.setattr(fake, "handler", unauthorized)
    status, body, text = await _json(await client.get("/api/deck/discord/status"))
    assert status == 200 and body["data"]["bot"]["ok"] is False
    assert "log in again" in body["data"]["bot"]["hint"]
    status, body, text2 = await _json(await client.post(
        f"/api/deck/discord/servers/{G}/moderate", json={"action": "kick", "user_id": MEMBER, "confirm": True}))
    assert status == 502 and body["status"] == 401
    _no_secrets(text)
    _no_secrets(text2)
