"""Who may do what to whom — the ONE moderation policy for every Discord surface.

The CLI, the deck routes and the bot's slash commands all call these functions
before any request reaches Discord, so a rule fixed here is fixed everywhere.

Rules (mirroring Discord's own model, checked up front so the answer is a clear
sentence instead of a 50013 after the fact):

* **Permission** — the actor needs the Discord permission for the action
  (Administrator implies all). The server owner has every permission.
* **Hierarchy** — the actor's highest role must sit strictly above the target's,
  unless the actor owns the server. The BOT's highest role must also sit above the
  target's, always — Discord enforces that one even for the owner.
* **Untouchables** — nobody acts on the server owner, on themselves, or on the bot.
* **No escalation** — a role can only be created or edited with permissions the
  actor already has, and only below the actor's (and the bot's) highest role.

``actor=None`` means the NAVIG operator acting AS the bot (the CLI): only the
bot-side rules apply, because the operator controls the bot outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Permission bits — https://discord.com/developers/docs/topics/permissions
KICK_MEMBERS = 1 << 1
BAN_MEMBERS = 1 << 2
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_GUILD = 1 << 5
MANAGE_ROLES = 1 << 28
MODERATE_MEMBERS = 1 << 40

#: The permission each action requires.
REQUIRED: dict[str, int] = {
    "kick": KICK_MEMBERS,
    "ban": BAN_MEMBERS,
    "unban": BAN_MEMBERS,
    "timeout": MODERATE_MEMBERS,
    "untimeout": MODERATE_MEMBERS,
    "role_assign": MANAGE_ROLES,
    "role_create": MANAGE_ROLES,
    "role_edit": MANAGE_ROLES,
    "role_delete": MANAGE_ROLES,
    "channel_create": MANAGE_CHANNELS,
    "channel_edit": MANAGE_CHANNELS,
    "channel_delete": MANAGE_CHANNELS,
}

#: Granting any of these via a role needs the server owner or an Administrator,
#: even when the actor holds the permission: a moderator who can manage roles must
#: not be able to mint an admin role.
DANGEROUS = ADMINISTRATOR | MANAGE_GUILD | MANAGE_ROLES | (1 << 29) | BAN_MEMBERS | (1 << 17)

#: Discord's limits.
MAX_TIMEOUT_SECONDS = 28 * 24 * 3600 - 60  # Discord's 28 days is exclusive
MAX_BAN_DELETE_SECONDS = 7 * 24 * 3600


class PolicyDenied(PermissionError):
    """The action is refused before anything is sent to Discord."""


@dataclass(frozen=True)
class Actor:
    """A member as the policy sees it: id, highest role position, effective permissions."""

    id: str
    top_position: int
    permissions: int
    is_owner: bool = False

    def has(self, perm: int) -> bool:
        return self.is_owner or bool(self.permissions & ADMINISTRATOR) or bool(self.permissions & perm)


def effective_permissions(role_ids: list[str], roles: list[dict], guild_id: str) -> int:
    """OR of @everyone (role id == guild id) and every role the member holds."""
    wanted = set(map(str, role_ids)) | {str(guild_id)}
    perms = 0
    for r in roles:
        if str(r.get("id")) in wanted:
            try:
                perms |= int(r.get("permissions") or 0)
            except (TypeError, ValueError):
                continue
    return perms


def top_position(role_ids: list[str], roles: list[dict]) -> int:
    """Highest position among the member's roles (0 = only @everyone)."""
    wanted = set(map(str, role_ids))
    return max((int(r.get("position") or 0) for r in roles if str(r.get("id")) in wanted), default=0)


_PERM_NAMES: dict[int, str] = {
    KICK_MEMBERS: "Kick Members",
    BAN_MEMBERS: "Ban Members",
    MANAGE_CHANNELS: "Manage Channels",
    MANAGE_ROLES: "Manage Roles",
    MODERATE_MEMBERS: "Timeout Members",
}


def _require(party: Actor, action: str, who: str) -> None:
    perm = REQUIRED[action]
    if not party.has(perm):
        raise PolicyDenied(f"{who} needs the {_PERM_NAMES.get(perm, hex(perm))} permission.")


def check_member_action(
    action: str,
    *,
    actor: Actor | None,
    bot: Actor,
    target_id: str,
    target_top_position: int,
    guild_owner_id: str,
) -> None:
    """Raise :class:`PolicyDenied` unless *actor* may perform *action* on *target_id*."""
    if action not in REQUIRED:
        raise ValueError(f"unknown action {action!r}")
    target_id = str(target_id)
    if target_id == str(guild_owner_id):
        raise PolicyDenied("Nobody can moderate the server owner.")
    if target_id == bot.id:
        raise PolicyDenied("The bot will not act on itself.")
    _require(bot, action, "The bot")
    if action != "unban" and not bot.is_owner and bot.top_position <= target_top_position:
        raise PolicyDenied("The bot's highest role must be above the member's — drag it higher in Server Settings → Roles.")
    if actor is None:
        return
    if target_id == actor.id:
        raise PolicyDenied("You can't do that to yourself.")
    _require(actor, action, "You")
    if action != "unban" and not actor.is_owner and actor.top_position <= target_top_position:
        raise PolicyDenied("Your highest role must be above the member's.")


def check_role_action(
    action: str,
    *,
    actor: Actor | None,
    bot: Actor,
    role_position: int | None,
    new_permissions: int | None = None,
    managed: bool = False,
) -> None:
    """Raise unless the role may be created/edited/deleted/assigned.

    *role_position* is the existing role's position (None when creating).
    *new_permissions* is the permission set the role would end up with, if changed.
    """
    if action not in ("role_assign", "role_create", "role_edit", "role_delete"):
        raise ValueError(f"unknown role action {action!r}")
    if managed:
        raise PolicyDenied("That role is managed by an integration (a bot or a boost) and can't be changed by hand.")
    parties: list[tuple[str, Actor]] = [("The bot", bot)]
    if actor is not None:
        parties.append(("You", actor))
    for who, party in parties:
        possessive = "Your" if who == "You" else "The bot's"
        _require(party, action, who)
        if role_position is not None and not party.is_owner and party.top_position <= role_position:
            raise PolicyDenied(f"{possessive} highest role must be above that role.")
        if (
            party is actor
            and action in ("role_create", "role_edit")
            and new_permissions is not None
            and new_permissions & DANGEROUS
            and not party.is_owner
            and not party.permissions & ADMINISTRATOR
        ):
            raise PolicyDenied(
                "Only the server owner or an Administrator can give a role Administrator, Manage Server, "
                "Manage Roles, Manage Webhooks, Ban Members or Mention Everyone."
            )
        if (
            action in ("role_create", "role_edit")
            and new_permissions is not None
            and not party.is_owner
            and not party.permissions & ADMINISTRATOR
        ):
            extra = new_permissions & ~party.permissions
            if extra:
                subject = "you" if who == "You" else "it"
                raise PolicyDenied(f"{who} can't grant permissions {subject} don't have (bits {extra:#x}).")


_DURATION = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(spec: str, *, maximum: int = MAX_TIMEOUT_SECONDS) -> int:
    """``"10m"`` / ``"2h"`` / ``"7d"`` / ``"1w"`` → seconds, bounded by *maximum*."""
    match = _DURATION.match(spec or "")
    if not match:
        raise ValueError(f"not a duration: {spec!r} (use e.g. 10m, 2h, 7d)")
    seconds = int(match.group(1)) * _UNIT[match.group(2).lower()]
    if seconds <= 0:
        raise ValueError("duration must be positive")
    if seconds > maximum:
        if seconds - maximum <= 60:
            return maximum  # "28d" means "as long as Discord allows"
        raise ValueError(f"duration {spec!r} exceeds the maximum of {(maximum + 3599) // 86400} days")
    return seconds


def audit_reason(reason: str | None, *, actor_name: str | None = None, actor_id: str | None = None) -> str:
    """The audit-log line: who asked for it through NAVIG, then why."""
    who = f"{actor_name} ({actor_id})" if actor_name and actor_id else (actor_name or actor_id or "operator")
    text = f"{who} via NAVIG"
    clean = " ".join((reason or "").split())  # no newlines / control runs in the audit log
    if clean:
        text += f": {clean}"
    return text[:512]


#: Names accepted by ``--permissions`` (snake_case, as in Discord's docs).
PERMISSIONS: dict[str, int] = {
    "create_invite": 1 << 0,
    "kick_members": KICK_MEMBERS,
    "ban_members": BAN_MEMBERS,
    "administrator": ADMINISTRATOR,
    "manage_channels": MANAGE_CHANNELS,
    "manage_guild": MANAGE_GUILD,
    "add_reactions": 1 << 6,
    "view_audit_log": 1 << 7,
    "view_channel": 1 << 10,
    "send_messages": 1 << 11,
    "manage_messages": 1 << 13,
    "embed_links": 1 << 14,
    "attach_files": 1 << 15,
    "read_message_history": 1 << 16,
    "mention_everyone": 1 << 17,
    "connect": 1 << 20,
    "speak": 1 << 21,
    "mute_members": 1 << 22,
    "deafen_members": 1 << 23,
    "move_members": 1 << 24,
    "change_nickname": 1 << 26,
    "manage_nicknames": 1 << 27,
    "manage_roles": MANAGE_ROLES,
    "manage_webhooks": 1 << 29,
    "manage_threads": 1 << 34,
    "moderate_members": MODERATE_MEMBERS,
}


def parse_permissions(spec: str) -> int:
    """``"kick_members,ban_members"`` or a raw integer → a permission bitfield."""
    spec = (spec or "").strip()
    if not spec:
        return 0
    if spec.isdigit():
        return int(spec)
    total = 0
    for name in spec.replace(" ", "").split(","):
        if not name:
            continue
        key = name.lower()
        if key not in PERMISSIONS:
            raise ValueError(f"unknown permission {name!r} (known: {', '.join(sorted(PERMISSIONS))})")
        total |= PERMISSIONS[key]
    return total


def parse_color(spec: str | None) -> int | None:
    """``"#ff8800"`` / ``"ff8800"`` / ``"0"`` → an RGB int (None = unchanged)."""
    if spec is None:
        return None
    text = spec.strip().lstrip("#")
    try:
        value = int(text, 16)
    except ValueError as exc:
        raise ValueError(f"not a colour: {spec!r} (use #rrggbb)") from exc
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError(f"colour out of range: {spec!r}")
    return value
