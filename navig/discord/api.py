"""Thin, honest Discord REST client (API v10) for the account manager.

One class, two identities (see ``navig.discord``). The identity decides the
``Authorization`` header and which operations are allowed:

* ``bot``     — everything below.
* ``account`` — only the owner's own membership: ``me``, ``guilds``, ``guild``,
  ``channels``, ``roles``, ``leave_guild``. Member listing and every write other
  than leaving refuse up front with :class:`IdentityNotAllowed`, before any
  request is sent.

The client sends one stable User-Agent for both identities — it does not
impersonate the Discord desktop client.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from navig.discord.config import Identity

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://navig.run, 1.0) navig"

#: Total seconds a single call may spend waiting out 429s before giving up.
MAX_RATE_LIMIT_WAIT = 30.0
MAX_RETRIES = 3

# Permission bits (https://discord.com/developers/docs/topics/permissions).
PERM_ADMINISTRATOR = 1 << 3
PERM_MANAGE_GUILD = 1 << 5

CHANNEL_TYPES: dict[int, str] = {
    0: "text",
    2: "voice",
    4: "category",
    5: "announcement",
    13: "stage",
    15: "forum",
    16: "media",
}
CHANNEL_TYPE_IDS: dict[str, int] = {v: k for k, v in CHANNEL_TYPES.items()}

#: Operations an ``account`` identity may perform. Everything else is bot-only.
ACCOUNT_ALLOWED = frozenset({"me", "guilds", "guild", "channels", "roles", "leave_guild"})


class DiscordAPIError(RuntimeError):
    """A Discord API call failed. ``status`` is HTTP, ``code`` is Discord's JSON error code."""

    def __init__(self, status: int, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def hint(self) -> str | None:
        """An actionable next step for the common failures, or None."""
        if self.status == 401:
            return "The token was rejected — log in again (navig discord login / navig discord bot set)."
        if self.code == 10004:
            return "Unknown server — check the id with: navig discord servers"
        if self.code == 10003:
            return "Unknown channel — check the id with: navig discord channels <server>"
        if self.code == 10007:
            return "Unknown member — that user is not in this server."
        if self.code == 10011:
            return "Unknown role — check the id with: navig discord roles <server>"
        if self.code == 10026:
            return "That user is not banned here."
        if self.code == 50001:
            return "Missing access — the identity is not in that server or cannot see that resource."
        if self.code == 50013:
            return "Missing permissions — give the bot's role the needed permission in Server Settings → Roles."
        return None


class IdentityNotAllowed(PermissionError):
    """The operation is not offered for this identity (see module docstring)."""


@dataclass(frozen=True)
class Guild:
    id: str
    name: str
    owner: bool
    permissions: int
    member_count: int | None
    online_count: int | None

    @property
    def is_admin(self) -> bool:
        return self.owner or bool(self.permissions & PERM_ADMINISTRATOR)

    @property
    def can_manage(self) -> bool:
        return self.is_admin or bool(self.permissions & PERM_MANAGE_GUILD)

    @classmethod
    def from_partial(cls, data: dict[str, Any]) -> Guild:
        try:
            perms = int(data.get("permissions") or 0)
        except (TypeError, ValueError):
            perms = 0
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or ""),
            owner=bool(data.get("owner")),
            permissions=perms,
            member_count=data.get("approximate_member_count"),
            online_count=data.get("approximate_presence_count"),
        )


class DiscordClient:
    """Synchronous Discord REST client bound to one identity and token."""

    def __init__(
        self,
        identity: Identity,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
        sleep=time.sleep,
    ) -> None:
        if not token:
            raise ValueError(f"no {identity} token")
        self.identity: Identity = identity
        auth = f"Bot {token}" if identity == "bot" else token
        self._http = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": auth, "User-Agent": USER_AGENT},
            timeout=timeout,
            transport=transport,
        )
        self._sleep = sleep

    # ── plumbing ────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DiscordClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _allow(self, op: str) -> None:
        if self.identity == "account" and op not in ACCOUNT_ALLOWED:
            raise IdentityNotAllowed(
                f"'{op}' is bot-only. Your account identity only lists, reads and leaves "
                "your own servers. Add the bot to that server (navig discord bot invite) "
                "and use --as bot."
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if reason:
            # Shows up in the server's audit log. URL-encoding is required for non-ASCII.
            from urllib.parse import quote

            headers["X-Audit-Log-Reason"] = quote(reason[:512])
        waited = 0.0
        for attempt in range(MAX_RETRIES + 1):
            resp = self._http.request(method, path, params=params, json=json, headers=headers)
            if resp.status_code == 429 and attempt < MAX_RETRIES:
                retry_after = _retry_after(resp)
                if waited + retry_after > MAX_RATE_LIMIT_WAIT:
                    break
                logger.debug("discord 429 on %s %s — waiting %.2fs", method, path, retry_after)
                self._sleep(retry_after)
                waited += retry_after
                continue
            break
        if resp.status_code == 204:
            return None
        if resp.is_success:
            return resp.json() if resp.content else None
        raise _error_from(resp)

    # ── identity ────────────────────────────────────────────────────────────

    def me(self) -> dict[str, Any]:
        self._allow("me")
        return self._request("GET", "/users/@me")

    def application(self) -> dict[str, Any]:
        """The bot's own application (its id is the OAuth2 ``client_id``)."""
        self._allow("application")
        return self._request("GET", "/oauth2/applications/@me")

    # ── servers ─────────────────────────────────────────────────────────────

    def guilds(self) -> list[Guild]:
        """Every server this identity is in (paged by 200, Discord's maximum)."""
        self._allow("guilds")
        out: list[Guild] = []
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 200, "with_counts": "true"}
            if after:
                params["after"] = after
            page = self._request("GET", "/users/@me/guilds", params=params) or []
            out.extend(Guild.from_partial(g) for g in page)
            if len(page) < 200:
                return out
            after = str(page[-1]["id"])

    def guild(self, guild_id: str) -> dict[str, Any]:
        self._allow("guild")
        return self._request("GET", f"/guilds/{guild_id}", params={"with_counts": "true"})

    def leave_guild(self, guild_id: str) -> None:
        self._allow("leave_guild")
        self._request("DELETE", f"/users/@me/guilds/{guild_id}")

    # ── channels ────────────────────────────────────────────────────────────

    def channels(self, guild_id: str) -> list[dict[str, Any]]:
        self._allow("channels")
        return self._request("GET", f"/guilds/{guild_id}/channels") or []

    def create_channel(
        self,
        guild_id: str,
        name: str,
        *,
        kind: str = "text",
        parent_id: str | None = None,
        topic: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self._allow("create_channel")
        if kind not in CHANNEL_TYPE_IDS:
            raise ValueError(f"unknown channel type {kind!r} (one of: {', '.join(CHANNEL_TYPE_IDS)})")
        body: dict[str, Any] = {"name": name, "type": CHANNEL_TYPE_IDS[kind]}
        if parent_id:
            body["parent_id"] = parent_id
        if topic:
            body["topic"] = topic
        return self._request("POST", f"/guilds/{guild_id}/channels", json=body, reason=reason)

    def edit_channel(self, channel_id: str, fields: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        self._allow("edit_channel")
        if not fields:
            raise ValueError("nothing to change")
        return self._request("PATCH", f"/channels/{channel_id}", json=fields, reason=reason)

    def delete_channel(self, channel_id: str, *, reason: str | None = None) -> dict[str, Any]:
        self._allow("delete_channel")
        return self._request("DELETE", f"/channels/{channel_id}", reason=reason)

    # ── roles ───────────────────────────────────────────────────────────────

    def roles(self, guild_id: str) -> list[dict[str, Any]]:
        self._allow("roles")
        return self._request("GET", f"/guilds/{guild_id}/roles") or []

    def add_member_role(self, guild_id: str, user_id: str, role_id: str, *, reason: str | None = None) -> None:
        self._allow("add_member_role")
        self._request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", reason=reason)

    def remove_member_role(self, guild_id: str, user_id: str, role_id: str, *, reason: str | None = None) -> None:
        self._allow("remove_member_role")
        self._request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", reason=reason)

    def create_role(self, guild_id: str, fields: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        self._allow("create_role")
        return self._request("POST", f"/guilds/{guild_id}/roles", json=fields, reason=reason)

    def edit_role(self, guild_id: str, role_id: str, fields: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        self._allow("edit_role")
        if not fields:
            raise ValueError("nothing to change")
        return self._request("PATCH", f"/guilds/{guild_id}/roles/{role_id}", json=fields, reason=reason)

    def delete_role(self, guild_id: str, role_id: str, *, reason: str | None = None) -> None:
        self._allow("delete_role")
        self._request("DELETE", f"/guilds/{guild_id}/roles/{role_id}", reason=reason)

    # ── moderation (bot only) ───────────────────────────────────────────────

    def member(self, guild_id: str, user_id: str) -> dict[str, Any]:
        self._allow("members")
        return self._request("GET", f"/guilds/{guild_id}/members/{user_id}")

    def kick(self, guild_id: str, user_id: str, *, reason: str | None = None) -> None:
        self._allow("kick")
        self._request("DELETE", f"/guilds/{guild_id}/members/{user_id}", reason=reason)

    def ban(self, guild_id: str, user_id: str, *, delete_message_seconds: int = 0, reason: str | None = None) -> None:
        self._allow("ban")
        self._request(
            "PUT", f"/guilds/{guild_id}/bans/{user_id}",
            json={"delete_message_seconds": int(delete_message_seconds)}, reason=reason,
        )

    def unban(self, guild_id: str, user_id: str, *, reason: str | None = None) -> None:
        self._allow("unban")
        self._request("DELETE", f"/guilds/{guild_id}/bans/{user_id}", reason=reason)

    def bans(self, guild_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        self._allow("bans")
        out: list[dict[str, Any]] = []
        after = "0"
        while len(out) < limit:
            size = min(1000, limit - len(out))
            page = self._request("GET", f"/guilds/{guild_id}/bans", params={"limit": size, "after": after}) or []
            out.extend(page)
            if len(page) < size:
                break
            after = str(page[-1]["user"]["id"])
        return out

    def timeout(self, guild_id: str, user_id: str, until_iso: str | None, *, reason: str | None = None) -> dict[str, Any]:
        """Time a member out until *until_iso* (ISO-8601), or lift it with ``None``."""
        self._allow("timeout")
        return self._request(
            "PATCH", f"/guilds/{guild_id}/members/{user_id}",
            json={"communication_disabled_until": until_iso}, reason=reason,
        )

    # ── the bot's own profile (bot only) ────────────────────────────────────

    def edit_me(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Change the bot user's username and/or avatar (a ``data:`` URI)."""
        self._allow("edit_me")
        if not fields:
            raise ValueError("nothing to change")
        return self._request("PATCH", "/users/@me", json=fields)

    def edit_application(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Change the application: ``description`` (the bot's About Me) or limited intent ``flags``."""
        self._allow("edit_application")
        if not fields:
            raise ValueError("nothing to change")
        return self._request("PATCH", "/applications/@me", json=fields)

    # ── members (bot only) ──────────────────────────────────────────────────

    def members(self, guild_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """List members (needs the Server Members intent enabled for the bot)."""
        self._allow("members")
        out: list[dict[str, Any]] = []
        after = "0"
        while len(out) < limit:
            page_size = min(1000, limit - len(out))
            page = self._request(
                "GET", f"/guilds/{guild_id}/members", params={"limit": page_size, "after": after}
            ) or []
            out.extend(page)
            if len(page) < page_size:
                break
            after = str(page[-1]["user"]["id"])
        return out

    def search_members(self, guild_id: str, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Members whose username or nickname starts with *query*."""
        self._allow("members")
        return self._request(
            "GET",
            f"/guilds/{guild_id}/members/search",
            params={"query": query, "limit": max(1, min(limit, 1000))},
        ) or []


def _retry_after(resp: httpx.Response) -> float:
    try:
        value = float((resp.json() or {}).get("retry_after", 1.0))
    except (ValueError, TypeError, AttributeError):
        try:
            value = float(resp.headers.get("Retry-After", 1.0))
        except (TypeError, ValueError):
            value = 1.0
    return max(0.0, value)


def _error_from(resp: httpx.Response) -> DiscordAPIError:
    message = resp.reason_phrase or f"HTTP {resp.status_code}"
    code: int | None = None
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        message = str(data.get("message") or message)
        raw_code = data.get("code")
        code = raw_code if isinstance(raw_code, int) else None
    return DiscordAPIError(resp.status_code, f"Discord API {resp.status_code}: {message}", code)


def parse_invite_code(spec: str) -> str:
    """``discord.gg/x``, ``https://discord.com/invite/x`` or a bare ``x`` → ``x``."""
    raw = spec.strip().rstrip("/")
    for marker in ("discord.gg/", "discord.com/invite/", "discordapp.com/invite/"):
        if marker in raw:
            raw = raw.split(marker, 1)[1]
            break
    code = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not code or not all(c.isalnum() or c == "-" for c in code):
        raise ValueError(f"not a Discord invite: {spec!r}")
    return code


def fetch_invite(code: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Resolve an invite without any token. Reading an invite joins nothing."""
    with httpx.Client(
        base_url=API_BASE, headers={"User-Agent": USER_AGENT}, timeout=15.0, transport=transport
    ) as http:
        resp = http.get(f"/invites/{code}", params={"with_counts": "true"})
    if resp.is_success:
        return resp.json()
    raise _error_from(resp)


def resolve_guild(guilds: list[Guild], spec: str) -> Guild:
    """Find a server by id, exact name, or unique case-insensitive name prefix."""
    spec = spec.strip()
    for g in guilds:
        if g.id == spec:
            return g
    lowered = spec.casefold()
    exact = [g for g in guilds if g.name.casefold() == lowered]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [g for g in guilds if g.name.casefold().startswith(lowered)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"no server matches {spec!r} — list them with: navig discord servers")
    names = ", ".join(f"{g.name} ({g.id})" for g in matches[:5])
    raise LookupError(f"{spec!r} matches {len(matches)} servers: {names} — use the id")


def bot_invite_url(application_id: str, *, admin: bool = False) -> str:
    """OAuth2 URL that adds the bot to a server the owner manages."""
    # View Channels · Send Messages · Manage Channels · Manage Roles · Kick · Ban ·
    # Timeout Members · Manage Server · Read History — exactly what navig discord uses.
    perms = 8 if admin else (1024 | 2048 | 16 | 268435456 | 2 | 4 | (1 << 40) | 32 | 65536)
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&scope=bot%20applications.commands&permissions={perms}"
    )
