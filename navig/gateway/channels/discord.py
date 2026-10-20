"""The NAVIG Discord bot — gateway channel (discord.py glue).

Every decision lives in :mod:`navig.discord.bot_logic` (who may reach the agent,
rate limits) and :mod:`navig.discord.moderation` (the one moderation path, shared
with the CLI). This module only translates between Discord and those.

Started by the gateway when ``discord.enabled`` is on, a bot token is in the vault
(``discord_bot_token``) and discord.py is installed (``pip install 'navig[discord]'``).

Slash commands (all replies ephemeral; moderation commands are server-only and
hidden by Discord from members without the matching permission):

    /navig  /status  /help
    /kick  /ban  /unban  /timeout  /untimeout
    /role add|remove|create|delete
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

try:
    import discord
    from discord import app_commands

    DISCORD_AVAILABLE = True
except ImportError:  # optional extra
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]
    DISCORD_AVAILABLE = False

from navig.discord import bot_logic

if TYPE_CHECKING:
    from navig.discord.settings import DiscordSettings

logger = logging.getLogger(__name__)

MessageHandler = Callable[[str, str, str, dict[str, Any]], Awaitable[Any]]

_MAX_MESSAGE = 2000


def is_discord_available() -> bool:
    return DISCORD_AVAILABLE


def _chunks(text: str, size: int = _MAX_MESSAGE) -> list[str]:
    text = str(text or "").strip() or "(no response)"
    return [text[i : i + size] for i in range(0, len(text), size)]


def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("text") or response.get("response") or response.get("content") or "")
    return str(response or "")


class DiscordChannel:
    """Bot lifecycle + Discord ⇄ NAVIG translation."""

    def __init__(self, token: str, settings: DiscordSettings, message_handler: MessageHandler | None) -> None:
        if not DISCORD_AVAILABLE:
            raise ImportError("discord.py is not installed — run: navig discord setup")
        if not token:
            raise ValueError("no Discord bot token")
        self._token = token
        self.settings = settings
        self.message_handler = message_handler
        self.cooldown = bot_logic.Cooldown(settings.cooldown_per_minute)
        self._task: asyncio.Task | None = None
        self._running = False
        # on_ready fires again after every reconnect; sync each scope once per process
        # so reconnects don't burn Discord's command-sync rate limit.
        self._synced: set[str] = set()

        # No privileged intents: slash commands, DMs and @mentions carry their content
        # without Message Content, and member data comes from REST.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)
        self._register_events()
        self._register_commands()

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="discord-bot")

    async def _run(self) -> None:
        try:
            await self.client.start(self._token)
        except discord.LoginFailure:
            logger.error("Discord bot: the token was rejected — run: navig discord setup")
        except discord.PrivilegedIntentsRequired as exc:
            logger.error("Discord bot: a privileged intent is not enabled: %s", exc)
        except Exception as exc:  # noqa: BLE001 — keep the gateway alive
            logger.error("Discord bot stopped: %s", exc)
        finally:
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if not self.client.is_closed():
            await self.client.close()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    def is_running(self) -> bool:
        return self._running and not self.client.is_closed()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _rest(self):
        from navig.discord.api import DiscordClient

        return DiscordClient("bot", self._token)

    async def _sync(self, guild: discord.abc.Snowflake | None = None) -> None:
        scope = "global" if self.settings.sync == "global" else str(getattr(guild, "id", ""))
        if not scope or scope in self._synced:
            return
        self._synced.add(scope)
        try:
            if self.settings.sync == "global":
                await self.tree.sync()
            elif guild is not None:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Discord: slash command sync failed for %s: %s", getattr(guild, "id", "global"), exc)

    async def _ask_agent(self, user_id: int, text: str, metadata: dict[str, Any]) -> str:
        if self.message_handler is None:
            return "The NAVIG agent is not available right now."
        try:
            return _response_text(await self.message_handler("discord", str(user_id), text, metadata))
        except Exception as exc:  # noqa: BLE001
            logger.error("Discord: agent call failed: %s", exc)
            return "⚠ The agent hit an error — check `navig doctor`."

    # ── events ──────────────────────────────────────────────────────────────

    def _register_events(self) -> None:
        client = self.client

        @client.event
        async def on_ready() -> None:
            logger.info("Discord bot online as %s in %d server(s)", client.user, len(client.guilds))
            for guild in list(client.guilds):
                if bot_logic.should_leave(self.settings, guild.id):
                    logger.warning("Discord: leaving %s (%s) — not in discord.allowed_guilds", guild.name, guild.id)
                    await guild.leave()
            if self.settings.sync == "global":
                await self._sync()
            else:
                for guild in client.guilds:
                    await self._sync(guild)

        @client.event
        async def on_guild_join(guild: discord.Guild) -> None:
            if bot_logic.should_leave(self.settings, guild.id):
                logger.warning("Discord: added to %s (%s) outside discord.allowed_guilds — leaving", guild.name, guild.id)
                await guild.leave()
                return
            logger.info("Discord: joined %s (%s)", guild.name, guild.id)
            if self.settings.sync == "guild":
                await self._sync(guild)

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author.bot or client.user is None:
                return
            in_dm = message.guild is None
            if not in_dm and client.user not in message.mentions:
                return
            # Owners only — everyone else gets silence, not a refusal to probe.
            if not bot_logic.agent_allowed(self.settings, message.author.id, None if in_dm else message.guild.id):
                return
            text = message.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
            if not text:
                return
            meta = {"channel_id": str(message.channel.id), "message_id": str(message.id),
                    "guild_id": None if in_dm else str(message.guild.id), "dm": in_dm}
            async with message.channel.typing():
                reply = await self._ask_agent(message.author.id, text, meta)
            for part in _chunks(reply):
                await message.channel.send(part, allowed_mentions=discord.AllowedMentions.none())

    # ── slash commands ──────────────────────────────────────────────────────

    async def _moderate(self, interaction: discord.Interaction, action: str, **kw: Any) -> None:
        """Rate-limit, optionally confirm, then run one moderation request."""
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not self.settings.guild_allowed(interaction.guild_id):
            await interaction.response.send_message("⛔ This server is not enabled for NAVIG.", ephemeral=True)
            return
        wait = self.cooldown.hit(f"{interaction.guild_id}:{interaction.user.id}")
        if wait:
            await interaction.response.send_message(f"⏳ Slow down — try again in {wait:.0f}s.", ephemeral=True)
            return

        async def run(target: discord.Interaction) -> None:
            reply = await asyncio.to_thread(
                bot_logic.moderate, self._rest, action,
                guild_id=str(interaction.guild_id), invoker_id=str(interaction.user.id),
                invoker_name=str(interaction.user), **kw,
            )
            await target.followup.send(reply, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        if self.settings.confirm and action in bot_logic.CONFIRMED:
            view = _ConfirmView(interaction.user.id, run)
            label = {"kick": "Kick", "ban": "Ban", "role_delete": "Delete role"}[action]
            subject = f"<@{kw['target_id']}>" if kw.get("target_id") else f"**{kw.get('role')}**"
            await interaction.response.send_message(
                f"{label} {subject}? Reason: {kw.get('reason') or '—'}", view=view, ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await run(interaction)

    def _register_commands(self) -> None:
        tree = self.tree
        Perms = discord.Permissions  # noqa: N806

        @tree.command(name="navig", description="Ask the NAVIG agent (owners only)")
        @app_commands.describe(message="What you want NAVIG to do")
        async def navig_cmd(interaction: discord.Interaction, message: str) -> None:
            if not bot_logic.agent_allowed(self.settings, interaction.user.id, interaction.guild_id):
                await interaction.response.send_message("⛔ Only this bot's owners can talk to NAVIG.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            reply = await self._ask_agent(interaction.user.id, message, {
                "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
                "channel_id": str(interaction.channel_id), "interaction": True,
            })
            for part in _chunks(reply):
                await interaction.followup.send(part, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        @tree.command(name="status", description="Is NAVIG online?")
        async def status_cmd(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                f"✅ NAVIG is online · {len(self.client.guilds)} server(s) · latency {self.client.latency * 1000:.0f} ms",
                ephemeral=True,
            )

        @tree.command(name="help", description="What NAVIG can do here")
        async def help_cmd(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                "**NAVIG** — server management\n"
                "• `/kick` `/ban` `/unban` `/timeout` `/untimeout` — moderators (needs the matching permission "
                "and a higher role than the member)\n"
                "• `/role add|remove|create|delete` — members with Manage Roles\n"
                "• `/navig` — the bot's owners only\n"
                "Every action is written to the server's audit log as `<you> via NAVIG`.",
                ephemeral=True,
            )

        if not self.settings.moderation_enabled:
            return

        @tree.command(name="kick", description="Kick a member")
        @app_commands.guild_only()
        @app_commands.default_permissions(kick_members=True)
        @app_commands.describe(member="Who", reason="Why (audit log)")
        async def kick_cmd(interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
            await self._moderate(interaction, "kick", target_id=str(member.id), reason=reason)

        @tree.command(name="ban", description="Ban a user")
        @app_commands.guild_only()
        @app_commands.default_permissions(ban_members=True)
        @app_commands.describe(user="Who", delete="Also delete their messages from the last … (e.g. 1h, 1d, max 7d)",
                               reason="Why (audit log)")
        async def ban_cmd(interaction: discord.Interaction, user: discord.User,
                          delete: str | None = None, reason: str | None = None) -> None:
            await self._moderate(interaction, "ban", target_id=str(user.id), delete=delete, reason=reason)

        @tree.command(name="unban", description="Lift a ban")
        @app_commands.guild_only()
        @app_commands.default_permissions(ban_members=True)
        @app_commands.describe(user_id="The banned user's id", reason="Why (audit log)")
        async def unban_cmd(interaction: discord.Interaction, user_id: str, reason: str | None = None) -> None:
            if not user_id.strip().isdigit():
                await interaction.response.send_message("⚠ That is not a user id.", ephemeral=True)
                return
            await self._moderate(interaction, "unban", target_id=user_id.strip(), reason=reason)

        @tree.command(name="timeout", description="Time a member out")
        @app_commands.guild_only()
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(member="Who", duration="How long: 10m, 2h, 3d … (max 28d)", reason="Why (audit log)")
        async def timeout_cmd(interaction: discord.Interaction, member: discord.Member,
                              duration: str, reason: str | None = None) -> None:
            await self._moderate(interaction, "timeout", target_id=str(member.id), duration=duration, reason=reason)

        @tree.command(name="untimeout", description="End a member's timeout")
        @app_commands.guild_only()
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(member="Who", reason="Why (audit log)")
        async def untimeout_cmd(interaction: discord.Interaction, member: discord.Member,
                                reason: str | None = None) -> None:
            await self._moderate(interaction, "untimeout", target_id=str(member.id), reason=reason)

        role = app_commands.Group(
            name="role", description="Manage roles", guild_only=True,
            default_permissions=Perms(manage_roles=True),
        )

        @role.command(name="add", description="Give a member a role")
        async def role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                           reason: str | None = None) -> None:
            await self._moderate(interaction, "role_add", target_id=str(member.id), role=str(role.id), reason=reason)

        @role.command(name="remove", description="Take a role from a member")
        async def role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                              reason: str | None = None) -> None:
            await self._moderate(interaction, "role_remove", target_id=str(member.id), role=str(role.id), reason=reason)

        @role.command(name="create", description="Create a role")
        @app_commands.describe(permissions="Comma-separated, e.g. kick_members,manage_messages", color="#rrggbb")
        async def role_create(interaction: discord.Interaction, name: str, permissions: str | None = None,
                              color: str | None = None, reason: str | None = None) -> None:
            await self._moderate(interaction, "role_create", role_name=name, permissions=permissions,
                                 color=color, reason=reason)

        @role.command(name="delete", description="Delete a role")
        async def role_delete(interaction: discord.Interaction, role: discord.Role, reason: str | None = None) -> None:
            await self._moderate(interaction, "role_delete", role=str(role.id), reason=reason)

        tree.add_command(role)


if DISCORD_AVAILABLE:

    class _ConfirmView(discord.ui.View):
        """Confirm / Cancel that only the member who asked can press, for 30 s."""

        def __init__(self, owner_id: int, on_confirm: Callable[[discord.Interaction], Awaitable[None]]) -> None:
            super().__init__(timeout=30)
            self.owner_id = owner_id
            self.on_confirm = on_confirm

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("⛔ Only the person who asked can confirm.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
            self.stop()
            await interaction.response.edit_message(content="Working…", view=None)
            await self.on_confirm(interaction)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
            self.stop()
            await interaction.response.edit_message(content="Cancelled.", view=None)


def create_discord_channel(message_handler: MessageHandler | None) -> DiscordChannel | None:
    """Build the bot from vault + config, or None (with the reason logged) when it should not run."""
    from navig.discord import config, settings

    s = settings.load()
    if not s.enabled:
        logger.info("Discord bot not started: discord.enabled is off (navig discord setup)")
        return None
    token = config.get_token("bot")
    if not token:
        logger.info("Discord bot not started: no bot token in the vault (navig discord setup)")
        return None
    if not DISCORD_AVAILABLE:
        logger.warning("Discord bot not started: discord.py is not installed (pip install 'navig[discord]')")
        return None
    if not s.owners:
        logger.info("Discord bot: no discord.owners — moderation works, the agent answers nobody")
    return DiscordChannel(token, s, message_handler)
