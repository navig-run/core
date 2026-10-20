"""Discord account manager — deck API.

The deck/HTTP face of ``navig.discord`` (the engine behind ``navig discord``). The
deck "Discord" page and the NAVIG OS Discord app both read and act through these
routes, and every moderation write goes through :mod:`navig.discord.moderation` —
the same policy check, audit-log reason and local modlog as the CLI and the bot's
slash commands. Nothing here re-implements a rule.

SECURITY: every route lives under ``/api/deck/*``, which is already behind the deck
auth middleware (owner / loopback only) — that IS the owner gate, so the operator
acts AS the bot (``actor=None``, ``surface="deck"``). Tokens are read from the vault
per request and never appear in any response, log line or error body.

Every mutating POST requires ``"confirm": true`` in its body (400 otherwise): the
UI sends it only after its own confirm step, so a stray request cannot kick or ban.

The Discord client is synchronous (httpx), so every call runs in a worker thread
(``asyncio.to_thread``) — the event loop never waits on Discord.

Routes (all under /api/deck/discord):
  GET  /status                         {bot, account, settings, vault_error}
  GET  /servers?as=auto|bot|account    every server + whether the bot is in it
  GET  /servers/{gid}?as=              guild + channels + roles (+ bot_present)
  GET  /servers/{gid}/members?limit=&q=   (bot)
  GET  /servers/{gid}/bans                (bot)
  GET  /modlog?guild_id=&limit=
  POST /servers/{gid}/moderate  {action, user_id, reason?, duration?, delete?, confirm}
  POST /servers/{gid}/roles     {op: create|edit|delete|add|remove, ..., confirm}
  POST /servers/{gid}/leave     {as: account|bot, confirm}

Errors: ``{"ok": false, "error": <sentence>, "kind": <kind>, ...}`` with
  409 not_configured (+ ``hint``) · 403 policy (+ ``reason``) · 403 identity ·
  400 invalid / confirm_required · 502 discord (+ ``status``, ``code``, ``hint``) ·
  502 unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - aiohttp always present at runtime
    web = None

logger = logging.getLogger(__name__)

SETUP_HINT = "Run: navig discord setup"
IDENTITIES = ("bot", "account")
MEMBER_ACTIONS = ("kick", "ban", "unban", "timeout", "untimeout")
ROLE_OPS = ("create", "edit", "delete", "add", "remove")
MAX_MEMBERS = 1000
MAX_MODLOG = 500


# ─── Envelope ────────────────────────────────────────────────────────────────


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _fail(http_status: int, error: str, kind: str, **extra: Any) -> "web.Response":
    # ``extra`` may itself carry a ``status`` field (Discord's HTTP status), so the
    # response status cannot be a parameter named ``status``.
    return web.json_response({"ok": False, "error": error, "kind": kind, **extra}, status=http_status)


class _NotConfigured(Exception):
    """No usable token for the identity the request needs."""

    def __init__(self, message: str, hint: str = SETUP_HINT) -> None:
        super().__init__(message)
        self.hint = hint


class _BadRequest(ValueError):
    """Request validation failed before anything was sent to Discord."""


def _error_response(exc: BaseException) -> "web.Response":
    """Map an engine exception to its documented HTTP shape."""
    import httpx

    from navig.discord.api import DiscordAPIError, IdentityNotAllowed
    from navig.discord.policy import PolicyDenied

    if isinstance(exc, _NotConfigured):
        return _fail(409, str(exc), "not_configured", hint=exc.hint)
    if isinstance(exc, PolicyDenied):
        return _fail(403, str(exc), "policy", reason=str(exc))
    if isinstance(exc, IdentityNotAllowed):
        return _fail(403, str(exc), "identity")
    if isinstance(exc, DiscordAPIError):
        return _fail(502, str(exc), "discord", status=exc.status, code=exc.code, hint=exc.hint)
    if isinstance(exc, (LookupError, ValueError)):
        return _fail(400, str(exc), "invalid")
    if isinstance(exc, httpx.HTTPError):
        return _fail(502, f"Could not reach Discord: {type(exc).__name__}", "unreachable")
    logger.exception("discord deck route failed")
    return _fail(500, f"{type(exc).__name__}: {exc}", "internal")


async def _call(fn: Callable[[], Any]) -> "web.Response":
    """Run blocking engine work off the event loop; envelope the result or the error."""
    try:
        return _ok(await asyncio.to_thread(fn))
    except Exception as exc:  # noqa: BLE001 — every failure is mapped to a status above
        return _error_response(exc)


async def _body(request: "web.Request") -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 — empty / malformed body → {}
        return {}
    return data if isinstance(data, dict) else {}


# ─── Validation ──────────────────────────────────────────────────────────────


def _snowflake(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text.isdigit() or len(text) > 25:
        raise _BadRequest(f"{field} must be a Discord id (digits only)")
    return text


def _guild_id(request: "web.Request") -> str:
    return _snowflake(request.match_info.get("guild_id"), "server id")


def _confirmed(body: dict[str, Any]) -> bool:
    return body.get("confirm") is True


def _confirm_required() -> "web.Response":
    return _fail(400, "This action changes your server — resend with \"confirm\": true.", "confirm_required")


def _int_param(raw: Any, default: int, *, lo: int, hi: int, field: str) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw))
    except ValueError as exc:
        raise _BadRequest(f"{field} must be a number") from exc
    return max(lo, min(hi, value))


def _seconds(raw: Any, *, maximum: int, field: str) -> int:
    """``"10m"`` / ``"2h"`` / ``"7d"`` or a plain number of seconds."""
    from navig.discord.policy import parse_duration

    if isinstance(raw, bool):
        raise _BadRequest(f"{field} must be a duration like 10m, 2h or 7d")
    if isinstance(raw, (int, float)):
        seconds = int(raw)
        if not 0 <= seconds <= maximum:
            raise _BadRequest(f"{field} must be between 0 and {maximum} seconds")
        return seconds
    text = str(raw).strip()
    if text.isdigit():
        return _seconds(int(text), maximum=maximum, field=field)
    return parse_duration(text, maximum=maximum)


def _text(raw: Any, *, limit: int = 512) -> str | None:
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    return text[:limit] or None


# ─── Identity + client ───────────────────────────────────────────────────────


def _setup_hint(identity: str) -> str:
    return "Run: navig discord setup (or navig discord bot set)" if identity == "bot" \
        else "Run: navig discord login"


def _not_set_up() -> _NotConfigured:
    from navig.discord import config

    reason = config.vault_unavailable_reason()
    if reason:
        return _NotConfigured(f"Cannot open the vault: {reason}", "Run: navig vault status")
    return _NotConfigured("Discord is not set up yet.", SETUP_HINT)


def _pick(as_: str | None, *, prefer: str) -> str:
    """Resolve ``as`` to an identity that has a token, or raise ``_NotConfigured``."""
    from navig.discord import config

    as_ = (as_ or "auto").strip().lower()
    if as_ not in ("auto", *IDENTITIES):
        raise _BadRequest("as must be auto, bot or account")
    if as_ != "auto":
        if not config.have_token(as_):  # type: ignore[arg-type]
            raise _NotConfigured(f"No {as_} token stored.", _setup_hint(as_))
        return as_
    for identity in (prefer, "account" if prefer == "bot" else "bot"):
        if config.have_token(identity):  # type: ignore[arg-type]
            return identity
    raise _not_set_up()


def _client(identity: str):
    """A client for *identity* — resolved through the module so tests can swap it."""
    from navig.discord import api, config

    token = config.get_token(identity)  # type: ignore[arg-type]
    if not token:
        raise _NotConfigured(f"No {identity} token stored.", _setup_hint(identity))
    return api.DiscordClient(identity, token)  # type: ignore[arg-type]


def _bot():
    return _client(_pick("bot", prefer="bot"))


# ─── Shapes ──────────────────────────────────────────────────────────────────


def _user_label(user: dict[str, Any]) -> str:
    return str(user.get("global_name") or user.get("username") or user.get("id") or "?")


def _guild_row(g: Any, bot_ids: set[str] | None) -> dict[str, Any]:
    return {
        "id": g.id,
        "name": g.name,
        "owner": g.owner,
        "admin": g.is_admin,
        "members": g.member_count,
        "online": g.online_count,
        "bot": (g.id in bot_ids) if bot_ids is not None else None,
    }


def _channel_row(c: dict[str, Any]) -> dict[str, Any]:
    from navig.discord.api import CHANNEL_TYPES

    kind = c.get("type")
    return {
        "id": str(c.get("id")),
        "name": c.get("name") or "",
        "type": kind,
        "kind": CHANNEL_TYPES.get(kind, f"type {kind}") if isinstance(kind, int) else "unknown",
        "parent_id": None if c.get("parent_id") is None else str(c.get("parent_id")),
        "position": c.get("position") or 0,
        "topic": c.get("topic") or None,
        "nsfw": bool(c.get("nsfw")),
    }


def _role_row(r: dict[str, Any], guild_id: str) -> dict[str, Any]:
    try:
        perms = int(r.get("permissions") or 0)
    except (TypeError, ValueError):
        perms = 0
    return {
        "id": str(r.get("id")),
        "name": r.get("name") or "",
        "position": int(r.get("position") or 0),
        "color": int(r.get("color") or 0),
        "permissions": str(perms),
        "admin": bool(perms & (1 << 3)),
        "managed": bool(r.get("managed")),
        "hoist": bool(r.get("hoist")),
        "mentionable": bool(r.get("mentionable")),
        "everyone": str(r.get("id")) == guild_id,
    }


def _member_row(m: dict[str, Any]) -> dict[str, Any]:
    user = m.get("user") or {}
    return {
        "id": str(user.get("id", "")),
        "username": user.get("username") or "",
        "display_name": m.get("nick") or _user_label(user),
        "nick": m.get("nick"),
        "bot": bool(user.get("bot")),
        "roles": [str(r) for r in m.get("roles") or []],
        "joined_at": m.get("joined_at"),
        "timed_out_until": m.get("communication_disabled_until"),
    }


def _ban_row(b: dict[str, Any]) -> dict[str, Any]:
    user = b.get("user") or {}
    return {
        "id": str(user.get("id", "")),
        "username": user.get("username") or "",
        "display_name": _user_label(user),
        "reason": b.get("reason"),
    }


# ─── Read handlers ───────────────────────────────────────────────────────────


def _status_sync() -> dict[str, Any]:
    from navig.discord import config, settings

    out: dict[str, Any] = {}
    for identity in IDENTITIES:
        token = config.get_token(identity)  # type: ignore[arg-type]
        row: dict[str, Any] = {"configured": bool(token), "ok": None, "user": None, "user_id": None,
                               "servers": None, "error": None, "hint": None}
        if token:
            try:
                with _client(identity) as client:
                    me = client.me()
                    row.update(ok=True, user=_user_label(me), user_id=str(me.get("id") or ""),
                               servers=len(client.guilds()))
            except Exception as exc:  # noqa: BLE001 — reported per identity
                from navig.discord.api import DiscordAPIError

                row.update(ok=False, error=str(exc) if isinstance(exc, DiscordAPIError) else type(exc).__name__,
                           hint=getattr(exc, "hint", None))
        else:
            row["hint"] = _setup_hint(identity)
        out[identity] = row
    s = settings.load()
    out["settings"] = {
        "enabled": s.enabled,
        "owners": sorted(s.owners),
        "allowed_guilds": sorted(s.allowed_guilds),
        "agent_enabled": s.agent_enabled,
        "moderation_enabled": s.moderation_enabled,
        "confirm": s.confirm,
        "cooldown_per_minute": s.cooldown_per_minute,
        "sync": s.sync,
    }
    out["vault_error"] = config.vault_unavailable_reason()
    out["setup_hint"] = SETUP_HINT
    return out


async def handle_discord_status(request: "web.Request") -> "web.Response":
    return await _call(_status_sync)


async def handle_discord_servers(request: "web.Request") -> "web.Response":
    as_ = request.query.get("as")

    def run() -> dict[str, Any]:
        from navig.discord import config

        identity = _pick(as_, prefer="account")
        with _client(identity) as client:
            guilds = client.guilds()
        bot_ids: set[str] | None = None
        bot_error: str | None = None
        if identity == "bot":
            bot_ids = {g.id for g in guilds}
        elif config.have_token("bot"):
            try:
                with _client("bot") as bot:
                    bot_ids = {g.id for g in bot.guilds()}
            except Exception as exc:  # noqa: BLE001 — the account list is still valid
                bot_error = str(exc)
        guilds.sort(key=lambda g: g.name.casefold())
        return {
            "identity": identity,
            "servers": [_guild_row(g, bot_ids) for g in guilds],
            "bot_error": bot_error,
        }

    return await _call(run)


def _detail_sync(guild_id: str, as_: str | None) -> dict[str, Any]:
    from navig.discord import config
    from navig.discord.api import DiscordAPIError

    requested = (as_ or "auto").strip().lower()
    order: list[str]
    if requested == "auto":
        order = [i for i in ("bot", "account") if config.have_token(i)]  # type: ignore[arg-type]
        if not order:
            raise _not_set_up()
    else:
        order = [_pick(requested, prefer="bot")]

    last: Exception | None = None
    for identity in order:
        try:
            with _client(identity) as client:
                guild = client.guild(guild_id)
                channels = client.channels(guild_id)
                roles = client.roles(guild_id)
        except DiscordAPIError as exc:
            # The bot is not in this server (or cannot see it): try the account next.
            if requested == "auto" and identity == "bot" and exc.status in (403, 404):
                last = exc
                continue
            raise
        channels_out = sorted((_channel_row(c) for c in channels), key=lambda c: (c["position"], c["name"]))
        roles_out = sorted((_role_row(r, guild_id) for r in roles), key=lambda r: -r["position"])
        return {
            "identity": identity,
            "bot_present": identity == "bot",
            "guild": {
                "id": str(guild.get("id") or guild_id),
                "name": guild.get("name") or "",
                "owner_id": str(guild.get("owner_id") or ""),
                "description": guild.get("description"),
                "members": guild.get("approximate_member_count"),
                "online": guild.get("approximate_presence_count"),
                "premium_tier": guild.get("premium_tier"),
                "verification_level": guild.get("verification_level"),
            },
            "channels": channels_out,
            "roles": roles_out,
        }
    raise last if last is not None else _not_set_up()


async def handle_discord_server(request: "web.Request") -> "web.Response":
    try:
        guild_id = _guild_id(request)
    except _BadRequest as exc:
        return _error_response(exc)
    as_ = request.query.get("as")
    return await _call(lambda: _detail_sync(guild_id, as_))


async def handle_discord_members(request: "web.Request") -> "web.Response":
    try:
        guild_id = _guild_id(request)
        limit = _int_param(request.query.get("limit"), 100, lo=1, hi=MAX_MEMBERS, field="limit")
    except _BadRequest as exc:
        return _error_response(exc)
    query = (request.query.get("q") or "").strip()

    def run() -> dict[str, Any]:
        with _bot() as client:
            rows = client.search_members(guild_id, query, limit=limit) if query \
                else client.members(guild_id, limit=limit)
        return {"members": [_member_row(m) for m in rows], "query": query or None, "limit": limit}

    return await _call(run)


async def handle_discord_bans(request: "web.Request") -> "web.Response":
    try:
        guild_id = _guild_id(request)
    except _BadRequest as exc:
        return _error_response(exc)

    def run() -> dict[str, Any]:
        with _bot() as client:
            return {"bans": [_ban_row(b) for b in client.bans(guild_id)]}

    return await _call(run)


async def handle_discord_modlog(request: "web.Request") -> "web.Response":
    raw_gid = request.query.get("guild_id")
    try:
        guild_id = _snowflake(raw_gid, "guild_id") if raw_gid else None
        limit = _int_param(request.query.get("limit"), 50, lo=1, hi=MAX_MODLOG, field="limit")
    except _BadRequest as exc:
        return _error_response(exc)

    def run() -> dict[str, Any]:
        from navig.discord import modlog

        return {"entries": modlog.tail(limit, guild_id)}

    return await _call(run)


# ─── Write handlers ──────────────────────────────────────────────────────────


async def handle_discord_moderate(request: "web.Request") -> "web.Response":
    body = await _body(request)
    try:
        from navig.discord.policy import MAX_BAN_DELETE_SECONDS, MAX_TIMEOUT_SECONDS

        guild_id = _guild_id(request)
        action = str(body.get("action") or "").strip().lower()
        if action not in MEMBER_ACTIONS:
            raise _BadRequest(f"action must be one of: {', '.join(MEMBER_ACTIONS)}")
        user_id = _snowflake(body.get("user_id"), "user_id")
        reason = _text(body.get("reason"))
        duration: int | None = None
        delete_seconds = 0
        if action == "timeout":
            if body.get("duration") in (None, ""):
                raise _BadRequest("timeout needs a duration (e.g. 10m, 2h, 7d)")
            duration = _seconds(body.get("duration"), maximum=MAX_TIMEOUT_SECONDS, field="duration")
            if duration <= 0:
                raise _BadRequest("duration must be positive")
        if action == "ban" and body.get("delete") not in (None, "", 0, "0"):
            delete_seconds = _seconds(body.get("delete"), maximum=MAX_BAN_DELETE_SECONDS, field="delete")
    except (_BadRequest, ValueError) as exc:
        return _error_response(exc)
    if not _confirmed(body):
        return _confirm_required()

    def run() -> dict[str, Any]:
        from navig.discord import moderation

        with _bot() as client:
            moderation.member_action(
                client, action, guild_id, user_id, reason=reason, surface="deck",
                duration_seconds=duration, delete_message_seconds=delete_seconds,
            )
        return {"action": action, "guild_id": guild_id, "user_id": user_id, "done": True}

    return await _call(run)


async def handle_discord_roles(request: "web.Request") -> "web.Response":
    body = await _body(request)
    try:
        from navig.discord.policy import parse_color, parse_permissions

        guild_id = _guild_id(request)
        op = str(body.get("op") or "").strip().lower()
        if op not in ROLE_OPS:
            raise _BadRequest(f"op must be one of: {', '.join(ROLE_OPS)}")
        reason = _text(body.get("reason"))
        role_spec = _text(body.get("role"), limit=100)
        if op != "create" and not role_spec:
            raise _BadRequest("role is required (its id or exact name)")
        user_id = _snowflake(body.get("user_id"), "user_id") if op in ("add", "remove") else None
        fields: dict[str, Any] = {}
        name = _text(body.get("name"), limit=100)
        if op == "create" and not name:
            raise _BadRequest("name is required to create a role")
        if op in ("create", "edit"):
            if name:
                fields["name"] = name
            if body.get("permissions") not in (None, ""):
                fields["permissions"] = parse_permissions(str(body.get("permissions")))
            if body.get("color") not in (None, ""):
                fields["color"] = parse_color(str(body.get("color")))
            for flag in ("hoist", "mentionable"):
                if flag in body and body[flag] is not None:
                    fields[flag] = bool(body[flag])
            if op == "edit" and not fields:
                raise _BadRequest("nothing to change — send name, permissions, color, hoist or mentionable")
    except (_BadRequest, ValueError) as exc:
        return _error_response(exc)
    if not _confirmed(body):
        return _confirm_required()

    def run() -> dict[str, Any]:
        from navig.discord import moderation

        with _bot() as client:
            if op == "create":
                role = moderation.role_create(
                    client, guild_id, fields["name"], permissions=int(fields.get("permissions", 0)),
                    color=fields.get("color"), hoist=bool(fields.get("hoist", False)),
                    mentionable=bool(fields.get("mentionable", False)), reason=reason, surface="deck",
                )
            elif op == "edit":
                role = moderation.role_edit(client, guild_id, str(role_spec), fields, reason=reason, surface="deck")
            elif op == "delete":
                role = moderation.role_delete(client, guild_id, str(role_spec), reason=reason, surface="deck")
            else:
                role = moderation.role_assign(
                    client, guild_id, str(user_id), str(role_spec), add=op == "add",
                    reason=reason, surface="deck",
                )
        return {"op": op, "guild_id": guild_id, "user_id": user_id,
                "role": _role_row(role or {}, guild_id) if role else None, "done": True}

    return await _call(run)


async def handle_discord_leave(request: "web.Request") -> "web.Response":
    body = await _body(request)
    try:
        guild_id = _guild_id(request)
        identity = str(body.get("as") or "").strip().lower()
        if identity not in IDENTITIES:
            raise _BadRequest("as must be account (you leave) or bot (the bot leaves)")
    except _BadRequest as exc:
        return _error_response(exc)
    if not _confirmed(body):
        return _confirm_required()

    def run() -> dict[str, Any]:
        from navig.discord.api import resolve_guild

        with _client(_pick(identity, prefer=identity)) as client:
            g = resolve_guild(client.guilds(), guild_id)
            if g.owner:
                who = "You own" if identity == "account" else "The bot owns"
                raise _BadRequest(f"{who} {g.name} — an owner cannot leave. Transfer ownership in Discord first.")
            client.leave_guild(g.id)
        return {"as": identity, "guild_id": g.id, "name": g.name, "left": True}

    return await _call(run)


# ─── Registration ────────────────────────────────────────────────────────────


def register(app: "web.Application") -> None:
    base = "/api/deck/discord"
    app.router.add_get(f"{base}/status", handle_discord_status)
    app.router.add_get(f"{base}/servers", handle_discord_servers)
    app.router.add_get(f"{base}/servers/{{guild_id}}", handle_discord_server)
    app.router.add_get(f"{base}/servers/{{guild_id}}/members", handle_discord_members)
    app.router.add_get(f"{base}/servers/{{guild_id}}/bans", handle_discord_bans)
    app.router.add_get(f"{base}/modlog", handle_discord_modlog)
    app.router.add_post(f"{base}/servers/{{guild_id}}/moderate", handle_discord_moderate)
    app.router.add_post(f"{base}/servers/{{guild_id}}/roles", handle_discord_roles)
    app.router.add_post(f"{base}/servers/{{guild_id}}/leave", handle_discord_leave)
