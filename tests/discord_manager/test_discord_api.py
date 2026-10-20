"""navig.discord.api — the REST client, against an in-memory Discord (httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from navig.discord.api import (
    DiscordAPIError,
    DiscordClient,
    Guild,
    IdentityNotAllowed,
    bot_invite_url,
    fetch_invite,
    parse_invite_code,
    resolve_guild,
)


def _client(identity, handler, **kw):
    return DiscordClient(identity, "tok", transport=httpx.MockTransport(handler), **kw)


def test_auth_header_differs_by_identity():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers["Authorization"])
        return httpx.Response(200, json={"id": "1", "username": "me"})

    _client("bot", handler).me()
    _client("account", handler).me()
    assert seen == ["Bot tok", "tok"]


def test_guilds_pages_until_a_short_page():
    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        calls.append(params)
        start = int(params.get("after", "0"))
        size = 200 if start == 0 else 3
        page = [{"id": str(start + i + 1), "name": f"g{start + i + 1}"} for i in range(size)]
        return httpx.Response(200, json=page)

    guilds = _client("account", handler).guilds()
    assert len(guilds) == 203
    assert calls[0]["with_counts"] == "true"
    assert calls[1]["after"] == "200"


def test_rate_limit_is_waited_out_then_retried():
    slept: list[float] = []
    attempts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"retry_after": 0.25, "global": False})
        return httpx.Response(200, json={"id": "1"})

    assert _client("bot", handler, sleep=slept.append).me() == {"id": "1"}
    assert slept == [0.25]


def test_rate_limit_beyond_the_budget_raises_instead_of_hanging():
    slept: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"retry_after": 120})

    with pytest.raises(DiscordAPIError) as err:
        _client("bot", handler, sleep=slept.append).me()
    assert err.value.status == 429
    assert slept == []


def test_error_carries_discord_code_and_a_hint():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Missing Permissions", "code": 50013})

    with pytest.raises(DiscordAPIError) as err:
        _client("bot", handler).roles("9")
    assert err.value.code == 50013
    assert "Missing Permissions" in str(err.value)
    assert "permission" in (err.value.hint or "")


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.members("1"),
        lambda c: c.search_members("1", "a"),
        lambda c: c.create_channel("1", "x"),
        lambda c: c.edit_channel("1", {"name": "x"}),
        lambda c: c.delete_channel("1"),
        lambda c: c.add_member_role("1", "2", "3"),
        lambda c: c.remove_member_role("1", "2", "3"),
        lambda c: c.application(),
    ],
)
def test_account_identity_refuses_bot_only_operations_before_any_request(call):
    sent: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(200, json={})

    with pytest.raises(IdentityNotAllowed):
        call(_client("account", handler))
    assert sent == []


def test_account_identity_may_leave_its_own_server():
    sent: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.method, req.url.path))
        return httpx.Response(204)

    assert _client("account", handler).leave_guild("42") is None
    assert sent == [("DELETE", "/api/v10/users/@me/guilds/42")]


def test_members_pages_with_after_and_respects_limit():
    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        calls.append(params)
        start = int(params["after"])
        size = int(params["limit"])
        return httpx.Response(200, json=[{"user": {"id": str(start + i + 1)}} for i in range(size)])

    rows = _client("bot", handler).members("1", limit=1500)
    assert len(rows) == 1500
    assert [c["limit"] for c in calls] == ["1000", "500"]
    assert calls[1]["after"] == "1000"


def test_audit_log_reason_is_url_encoded():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["reason"] = req.headers.get("X-Audit-Log-Reason", "")
        seen["body"] = req.content.decode()
        return httpx.Response(201, json={"id": "5", "name": "général"})

    _client("bot", handler).create_channel("1", "général", kind="voice", reason="réorg salon")
    assert seen["reason"] == "r%C3%A9org%20salon"
    assert json.loads(seen["body"]) == {"name": "général", "type": 2}


def test_unknown_channel_type_is_rejected_locally():
    with pytest.raises(ValueError):
        _client("bot", lambda r: httpx.Response(200)).create_channel("1", "x", kind="hologram")


@pytest.mark.parametrize(
    "spec,code",
    [
        ("abc123", "abc123"),
        ("discord.gg/abc123", "abc123"),
        ("https://discord.gg/abc-123/", "abc-123"),
        ("https://discord.com/invite/abc123?event=1", "abc123"),
        ("http://discordapp.com/invite/abc123", "abc123"),
    ],
)
def test_parse_invite_code(spec, code):
    assert parse_invite_code(spec) == code


@pytest.mark.parametrize("spec", ["", "not an invite!", "discord.gg/", "../etc"])
def test_parse_invite_code_rejects_garbage(spec):
    with pytest.raises(ValueError):
        parse_invite_code(spec)


def test_fetch_invite_sends_no_token():
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json={"code": "x", "guild": {"name": "G"}})

    assert fetch_invite("x", transport=httpx.MockTransport(handler))["guild"]["name"] == "G"
    assert seen["auth"] is None


def _g(id_: str, name: str, **kw) -> Guild:
    return Guild(id=id_, name=name, owner=kw.get("owner", False), permissions=kw.get("perms", 0),
                 member_count=None, online_count=None)


def test_resolve_guild_by_id_exact_name_and_unique_prefix():
    guilds = [_g("1", "Python"), _g("2", "Python Brasil"), _g("3", "Rust")]
    assert resolve_guild(guilds, "2").id == "2"
    assert resolve_guild(guilds, "python").id == "1"  # exact beats prefix
    assert resolve_guild(guilds, "ru").id == "3"
    with pytest.raises(LookupError, match="matches 2"):
        resolve_guild([_g("1", "Python A"), _g("2", "Python B")], "pyth")
    with pytest.raises(LookupError, match="no server"):
        resolve_guild(guilds, "Go")


def test_guild_admin_flags():
    assert _g("1", "a", owner=True).is_admin
    assert _g("1", "a", perms=8).is_admin
    assert not _g("1", "a", perms=32).is_admin
    assert _g("1", "a", perms=32).can_manage
    assert Guild.from_partial({"id": 1, "name": "x", "permissions": "bogus"}).permissions == 0


def test_bot_invite_url():
    assert "client_id=77" in bot_invite_url("77")
    assert bot_invite_url("77", admin=True).endswith("permissions=8")
