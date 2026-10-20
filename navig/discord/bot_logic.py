"""The NAVIG Discord bot's decisions, free of discord.py so they can be tested.

``navig.gateway.channels.discord`` is the thin discord.py glue; every decision it
makes — who may reach the agent, who may moderate, how often — is made here.

Security model (fail-closed):

* **The agent** (``/navig``, DMs, @mentions) answers ONLY ``discord.owners``. An empty
  owner list means nobody: a bot sitting in a public server is never a shell into the
  operator's machine. Non-owners get no reply on message paths (no oracle), and an
  ephemeral refusal on the slash command.
* **Moderation** is open to any member of the server who holds the Discord permission
  for the action AND outranks the target — the same policy the CLI enforces, through the
  same service (``navig.discord.moderation``), using the bot's token over REST.
* **Rate limit** — each member gets ``discord.moderation.cooldown`` moderation commands
  per rolling minute.
* **Servers** — when ``discord.allowed_guilds`` is set the bot works only there and
  leaves any other server it is added to.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from navig.discord.settings import DiscordSettings

#: Moderation actions the bot exposes, with the slash-command verb.
MEMBER_ACTIONS = ("kick", "ban", "unban", "timeout", "untimeout")
#: Actions that need a button press when ``discord.moderation.confirm`` is on.
CONFIRMED = frozenset({"kick", "ban", "role_delete"})


def agent_allowed(settings: DiscordSettings, user_id: int | str, guild_id: int | str | None) -> bool:
    """May this user talk to the NAVIG agent here?"""
    if not settings.agent_enabled or not settings.owners:
        return False
    if not settings.is_owner(user_id):
        return False
    return guild_id is None or settings.guild_allowed(guild_id)


def should_leave(settings: DiscordSettings, guild_id: int | str) -> bool:
    """True when the bot was added to a server outside ``discord.allowed_guilds``."""
    return bool(settings.allowed_guilds) and not settings.guild_allowed(guild_id)


class Cooldown:
    """At most *per_minute* events per key in any rolling 60 s window."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = max(1, int(per_minute))
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> float:
        """Record an attempt. Returns 0 when allowed, else seconds until the next slot."""
        now = self._clock()
        q = self._events[key]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return max(0.1, 60 - (now - q[0]))
        q.append(now)
        return 0.0


def moderate(
    client_factory: Callable[[], Any],
    action: str,
    *,
    guild_id: str,
    invoker_id: str,
    invoker_name: str,
    target_id: str | None = None,
    reason: str | None = None,
    duration: str | None = None,
    delete: str | None = None,
    role: str | None = None,
    role_name: str | None = None,
    permissions: str | None = None,
    color: str | None = None,
) -> str:
    """Run one moderation request for a Discord member; return the reply text.

    Blocking (REST) — the glue calls it through ``asyncio.to_thread``. Never raises:
    every refusal and failure becomes a ⛔/⚠ sentence the member can act on.
    """
    from navig.discord import moderation, policy
    from navig.discord.api import DiscordAPIError

    try:
        with client_factory() as client:
            ctx = moderation.context(client, guild_id)
            actor = ctx.actor_for(client.member(guild_id, invoker_id))
            common: dict[str, Any] = {
                "reason": reason, "actor": actor, "actor_name": invoker_name,
                "surface": "discord", "ctx": ctx,
            }
            if action in MEMBER_ACTIONS:
                kw: dict[str, Any] = {}
                if action == "timeout":
                    kw["duration_seconds"] = policy.parse_duration(duration or "")
                if action == "ban" and delete:
                    kw["delete_message_seconds"] = policy.parse_duration(
                        delete, maximum=policy.MAX_BAN_DELETE_SECONDS)
                moderation.member_action(client, action, guild_id, str(target_id), **common, **kw)
                verb = {"kick": "Kicked", "ban": "Banned", "unban": "Unbanned",
                        "timeout": f"Timed out ({duration})", "untimeout": "Lifted the timeout on"}[action]
                return f"✅ {verb} <@{target_id}>."
            if action in ("role_add", "role_remove"):
                r = moderation.role_assign(client, guild_id, str(target_id), str(role), add=action == "role_add", **common)
                return f"✅ {'Gave' if action == 'role_add' else 'Took'} **{r.get('name')}** " \
                       f"{'to' if action == 'role_add' else 'from'} <@{target_id}>."
            if action == "role_create":
                r = moderation.role_create(
                    client, guild_id, str(role_name),
                    permissions=policy.parse_permissions(permissions or ""),
                    color=policy.parse_color(color), **common)
                return f"✅ Created role **{r.get('name')}**."
            if action == "role_delete":
                r = moderation.role_delete(client, guild_id, str(role), **common)
                return f"✅ Deleted role **{r.get('name')}**."
            return f"⚠ Unknown action {action!r}."
    except policy.PolicyDenied as exc:
        return f"⛔ {exc}"
    except (LookupError, ValueError) as exc:
        return f"⚠ {exc}"
    except DiscordAPIError as exc:
        return f"⚠ {exc}" + (f"\n{exc.hint}" if exc.hint else "")
    except Exception as exc:  # noqa: BLE001 — a reply beats a silent failure
        return f"⚠ Something went wrong: {type(exc).__name__}"
