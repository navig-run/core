"""Moderation service — the single code path for every moderation action.

Every surface (CLI, deck route, the bot's slash commands) calls these functions
with a bot :class:`~navig.discord.api.DiscordClient`. Each one:

1. reads what the policy needs (server owner, roles, the bot's member, the target's),
2. asks :mod:`navig.discord.policy` — a refusal raises ``PolicyDenied`` and nothing
   is sent,
3. performs the action with a ``"<who> via NAVIG: <reason>"`` audit-log reason,
4. records it in the local modlog (success or failure).

``actor`` is the member who asked (slash commands, deck with a signed-in member);
``None`` is the operator acting as the bot (CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from navig.discord import modlog, policy
from navig.discord.api import DiscordAPIError, DiscordClient
from navig.discord.policy import Actor, PolicyDenied


@dataclass
class GuildContext:
    """Everything the policy needs about one server, fetched once per action."""

    guild_id: str
    owner_id: str
    roles: list[dict[str, Any]]
    bot: Actor

    def actor_for(self, member: dict[str, Any]) -> Actor:
        user_id = str((member.get("user") or {}).get("id", ""))
        role_ids = [str(r) for r in member.get("roles") or []]
        return Actor(
            id=user_id,
            top_position=policy.top_position(role_ids, self.roles),
            permissions=policy.effective_permissions(role_ids, self.roles, self.guild_id),
            is_owner=user_id == self.owner_id,
        )

    def role(self, spec: str) -> dict[str, Any]:
        for r in self.roles:
            if str(r.get("id")) == spec:
                return r
        hits = [r for r in self.roles if str(r.get("name", "")).casefold() == spec.casefold()]
        if len(hits) != 1:
            raise LookupError(f"{'no' if not hits else 'more than one'} role named {spec!r} — use its id")
        return hits[0]


def context(client: DiscordClient, guild_id: str) -> GuildContext:
    guild = client.guild(guild_id)
    roles = client.roles(guild_id)
    bot_id = str(client.me()["id"])
    owner_id = str(guild.get("owner_id"))
    ctx = GuildContext(guild_id=str(guild_id), owner_id=owner_id, roles=roles, bot=Actor("", 0, 0))
    ctx.bot = ctx.actor_for(client.member(guild_id, bot_id))
    return ctx


def _target(client: DiscordClient, ctx: GuildContext, user_id: str) -> tuple[int, int]:
    """The target's (highest role position, permissions); (0, 0) when not a member."""
    try:
        member = client.member(ctx.guild_id, user_id)
    except DiscordAPIError as exc:
        if exc.code == 10007:
            return 0, 0
        raise
    role_ids = [str(r) for r in member.get("roles") or []]
    return (policy.top_position(role_ids, ctx.roles),
            policy.effective_permissions(role_ids, ctx.roles, ctx.guild_id))


def _target_position(client: DiscordClient, ctx: GuildContext, user_id: str) -> int:
    return _target(client, ctx, user_id)[0]


def _run(action: str, ctx: GuildContext, target: str | None, actor: Actor | None,
         surface: str, reason: str | None, fn) -> Any:
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised unchanged
        modlog.record(action, guild_id=ctx.guild_id, target_id=target, actor_id=actor.id if actor else None,
                      surface=surface, reason=reason, ok=False, detail=str(exc))
        raise
    modlog.record(action, guild_id=ctx.guild_id, target_id=target, actor_id=actor.id if actor else None,
                  surface=surface, reason=reason, ok=True)
    return result


def member_action(
    client: DiscordClient,
    action: str,
    guild_id: str,
    user_id: str,
    *,
    reason: str | None = None,
    actor: Actor | None = None,
    actor_name: str | None = None,
    surface: str = "cli",
    duration_seconds: int | None = None,
    delete_message_seconds: int = 0,
    ctx: GuildContext | None = None,
) -> Any:
    """kick · ban · unban · timeout · untimeout one member, after the policy says yes."""
    ctx = ctx or context(client, guild_id)
    user_id = str(user_id)
    position, target_perms = (0, 0) if action == "unban" else _target(client, ctx, user_id)
    if action == "timeout" and target_perms & policy.ADMINISTRATOR:
        raise PolicyDenied("Discord does not allow timing out an Administrator.")
    policy.check_member_action(
        action, actor=actor, bot=ctx.bot, target_id=user_id,
        target_top_position=position, guild_owner_id=ctx.owner_id,
    )
    audit = policy.audit_reason(reason, actor_name=actor_name, actor_id=actor.id if actor else None)

    if action == "kick":
        fn = lambda: client.kick(guild_id, user_id, reason=audit)  # noqa: E731
    elif action == "ban":
        if not 0 <= delete_message_seconds <= policy.MAX_BAN_DELETE_SECONDS:
            raise ValueError("delete_message_seconds must be between 0 and 7 days")
        fn = lambda: client.ban(guild_id, user_id, delete_message_seconds=delete_message_seconds, reason=audit)  # noqa: E731
    elif action == "unban":
        fn = lambda: client.unban(guild_id, user_id, reason=audit)  # noqa: E731
    elif action == "timeout":
        if not duration_seconds or not 0 < duration_seconds <= policy.MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout needs a duration between 1 second and 28 days")
        until = (datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)).isoformat()
        fn = lambda: client.timeout(guild_id, user_id, until, reason=audit)  # noqa: E731
    elif action == "untimeout":
        fn = lambda: client.timeout(guild_id, user_id, None, reason=audit)  # noqa: E731
    else:
        raise ValueError(f"unknown member action {action!r}")
    return _run(action, ctx, user_id, actor, surface, reason, fn)


def role_assign(
    client: DiscordClient, guild_id: str, user_id: str, role_spec: str, *, add: bool,
    reason: str | None = None, actor: Actor | None = None, actor_name: str | None = None,
    surface: str = "cli", ctx: GuildContext | None = None,
) -> dict[str, Any]:
    ctx = ctx or context(client, guild_id)
    role = ctx.role(role_spec)
    policy.check_role_action("role_assign", actor=actor, bot=ctx.bot,
                             role_position=int(role.get("position") or 0), managed=bool(role.get("managed")))
    # Assigning a role also acts ON a member: the hierarchy over the member applies too.
    if actor is not None:
        position = _target_position(client, ctx, str(user_id))
        if str(user_id) != actor.id and not actor.is_owner and actor.top_position <= position:
            raise PolicyDenied("Your highest role must be above the member's.")
    audit = policy.audit_reason(reason, actor_name=actor_name, actor_id=actor.id if actor else None)
    rid = str(role["id"])
    fn = (lambda: client.add_member_role(guild_id, str(user_id), rid, reason=audit)) if add \
        else (lambda: client.remove_member_role(guild_id, str(user_id), rid, reason=audit))
    _run("role_add" if add else "role_remove", ctx, str(user_id), actor, surface, reason, fn)
    return role


def role_create(
    client: DiscordClient, guild_id: str, name: str, *, permissions: int = 0, color: int | None = None,
    hoist: bool = False, mentionable: bool = False, reason: str | None = None,
    actor: Actor | None = None, actor_name: str | None = None, surface: str = "cli",
    ctx: GuildContext | None = None,
) -> dict[str, Any]:
    ctx = ctx or context(client, guild_id)
    policy.check_role_action("role_create", actor=actor, bot=ctx.bot, role_position=None,
                             new_permissions=int(permissions))
    fields: dict[str, Any] = {"name": name, "permissions": str(int(permissions)),
                              "hoist": bool(hoist), "mentionable": bool(mentionable)}
    if color is not None:
        fields["color"] = int(color)
    audit = policy.audit_reason(reason, actor_name=actor_name, actor_id=actor.id if actor else None)
    return _run("role_create", ctx, None, actor, surface, reason,
                lambda: client.create_role(guild_id, fields, reason=audit))


def role_edit(
    client: DiscordClient, guild_id: str, role_spec: str, fields: dict[str, Any], *,
    reason: str | None = None, actor: Actor | None = None, actor_name: str | None = None,
    surface: str = "cli", ctx: GuildContext | None = None,
) -> dict[str, Any]:
    ctx = ctx or context(client, guild_id)
    role = ctx.role(role_spec)
    new_perms = int(fields["permissions"]) if "permissions" in fields else None
    policy.check_role_action("role_edit", actor=actor, bot=ctx.bot,
                             role_position=int(role.get("position") or 0),
                             new_permissions=new_perms, managed=bool(role.get("managed")))
    if new_perms is not None:
        fields = {**fields, "permissions": str(new_perms)}
    audit = policy.audit_reason(reason, actor_name=actor_name, actor_id=actor.id if actor else None)
    return _run("role_edit", ctx, str(role["id"]), actor, surface, reason,
                lambda: client.edit_role(guild_id, str(role["id"]), fields, reason=audit))


def role_delete(
    client: DiscordClient, guild_id: str, role_spec: str, *, reason: str | None = None,
    actor: Actor | None = None, actor_name: str | None = None, surface: str = "cli",
    ctx: GuildContext | None = None,
) -> dict[str, Any]:
    ctx = ctx or context(client, guild_id)
    role = ctx.role(role_spec)
    if str(role["id"]) == ctx.guild_id:
        raise PolicyDenied("@everyone can't be deleted.")
    policy.check_role_action("role_delete", actor=actor, bot=ctx.bot,
                             role_position=int(role.get("position") or 0), managed=bool(role.get("managed")))
    audit = policy.audit_reason(reason, actor_name=actor_name, actor_id=actor.id if actor else None)
    _run("role_delete", ctx, str(role["id"]), actor, surface, reason,
         lambda: client.delete_role(guild_id, str(role["id"]), reason=audit))
    return role
