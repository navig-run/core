"""Non-secret Discord settings (``discord.*`` in config.yaml) — the documented keys.

Secrets never live here: tokens are in the vault (``navig.discord.config``).

| key | default | meaning |
|-----|---------|---------|
| ``discord.enabled`` | false | the daemon starts the bot |
| ``discord.owners`` | [] | Discord user ids allowed to talk to the NAVIG agent (``/navig``) and run owner commands. **Empty = nobody**: the agent is never open to the world |
| ``discord.allowed_guilds`` | [] | if set, the bot works only in these servers and leaves any other it is added to |
| ``discord.agent.enabled`` | true | owners may use ``/navig`` |
| ``discord.moderation.enabled`` | true | the moderation slash commands are registered |
| ``discord.moderation.confirm`` | true | kick / ban / role delete ask for a button confirmation |
| ``discord.moderation.cooldown`` | 5 | max moderation commands per member per minute |
| ``discord.sync`` | guild | where slash commands are published: ``guild`` (instant, per server) or ``global`` (up to an hour) |

``navig config set`` stores strings, so every boolean goes through ``coerce_bool``
and every list accepts a real list or a comma-separated string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navig.core.coerce import coerce_bool

PREFIX = "discord"


def _cfg() -> Any:
    from navig.config import get_config_manager

    return get_config_manager()


def _get(key: str, default: Any) -> Any:
    try:
        return _cfg().get(f"{PREFIX}.{key}", default)
    except Exception:  # noqa: BLE001 — unreadable config → documented default
        return default


def _ids(value: Any) -> frozenset[str]:
    if value is None or value == "":
        return frozenset()
    items = value if isinstance(value, (list, tuple, set, frozenset)) else str(value).split(",")
    out = set()
    for item in items:
        text = str(item).strip()
        if text.isdigit():
            out.add(text)
    return frozenset(out)


@dataclass(frozen=True)
class DiscordSettings:
    enabled: bool = False
    owners: frozenset[str] = field(default_factory=frozenset)
    allowed_guilds: frozenset[str] = field(default_factory=frozenset)
    agent_enabled: bool = True
    moderation_enabled: bool = True
    confirm: bool = True
    cooldown_per_minute: int = 5
    sync: str = "guild"

    def is_owner(self, user_id: int | str) -> bool:
        return str(user_id) in self.owners

    def guild_allowed(self, guild_id: int | str | None) -> bool:
        return not self.allowed_guilds or (guild_id is not None and str(guild_id) in self.allowed_guilds)


def load() -> DiscordSettings:
    try:
        cooldown = int(_get("moderation.cooldown", 5))
    except (TypeError, ValueError):
        cooldown = 5
    sync = str(_get("sync", "guild") or "guild").strip().lower()
    return DiscordSettings(
        enabled=coerce_bool(_get("enabled", False), default=False),
        owners=_ids(_get("owners", [])),
        allowed_guilds=_ids(_get("allowed_guilds", [])),
        agent_enabled=coerce_bool(_get("agent.enabled", True), default=True),
        moderation_enabled=coerce_bool(_get("moderation.enabled", True), default=True),
        confirm=coerce_bool(_get("moderation.confirm", True), default=True),
        cooldown_per_minute=max(1, cooldown),
        sync=sync if sync in ("guild", "global") else "guild",
    )


def set_value(key: str, value: Any) -> None:
    """Write one ``discord.<key>`` value (the safe dotted write)."""
    _cfg().set_global(f"{PREFIX}.{key}", value)
