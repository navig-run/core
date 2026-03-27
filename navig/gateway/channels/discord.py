"""
NAVIG Discord Channel Adapter

Discord bot integration for NAVIG Gateway.
Supports:
- Direct messages
- Server channels (with @mentions)
- Slash commands
- Message history for context
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import discord
    from discord import app_commands
    from discord.ext import commands

try:
    import discord
    from discord import app_commands
    from discord.ext import commands

    DISCORD_AVAILABLE = True
except ImportError:
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]
    commands = None  # type: ignore[assignment]
    DISCORD_AVAILABLE = False

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class DiscordChannelConfig:
    """Configuration for Discord channel."""

    def __init__(
        self,
        token: str | None = None,
        allowed_guilds: list[int] | None = None,
        allowed_users: list[int] | None = None,
        allowed_channels: list[int] | None = None,
        command_prefix: str = "!navig",
        respond_to_mentions: bool = True,
        respond_to_dms: bool = True,
    ):
        """
        Initialize Discord config.

        Args:
            token: Discord bot token (from env if not provided)
            allowed_guilds: List of allowed guild/server IDs (None = all)
            allowed_users: List of allowed user IDs (None = all)
            allowed_channels: List of allowed channel IDs (None = all)
            command_prefix: Prefix for text commands
            respond_to_mentions: Whether to respond to @mentions
            respond_to_dms: Whether to respond to direct messages
        """
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.allowed_guilds = allowed_guilds
        self.allowed_users = allowed_users
        self.allowed_channels = allowed_channels
        self.command_prefix = command_prefix
        self.respond_to_mentions = respond_to_mentions
        self.respond_to_dms = respond_to_dms

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscordChannelConfig:
        """Create config from dictionary."""
        return cls(
            token=data.get("token"),
            allowed_guilds=data.get("allowed_guilds"),
            allowed_users=data.get("allowed_users"),
            allowed_channels=data.get("allowed_channels"),
            command_prefix=data.get("command_prefix", "!navig"),
            respond_to_mentions=data.get("respond_to_mentions", True),
            respond_to_dms=data.get("respond_to_dms", True),
        )


class DiscordChannel:
    """
    Discord channel adapter for NAVIG Gateway.

    Handles:
    - Bot lifecycle management
    - Message routing to gateway
    - Response formatting for Discord
    - Permission checking
    """

    def __init__(
        self,
        config: DiscordChannelConfig,
        message_handler: Callable[[str, str, str, dict[str, Any]], asyncio.Future],
    ):
        """
        Initialize Discord channel.

        Args:
            config: Discord channel configuration
            message_handler: Async callback for message routing
                            (channel, user_id, message, metadata) -> response
        """
        if not DISCORD_AVAILABLE:
            raise ImportError(
                "discord.py is required for Discord channel. Install: pip install discord.py"
            )

        self.config = config
        self.message_handler = message_handler

        # Set up intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        intents.guild_messages = True

        # Create bot
        self.bot = commands.Bot(
            command_prefix=config.command_prefix,
            intents=intents,
            help_command=None,
        )

        self._setup_events()
        self._setup_commands()

        self._running = False

    def _setup_events(self):
        """Set up Discord event handlers."""

        @self.bot.event
        async def on_ready():
            logger.info(f"Discord bot connected as {self.bot.user}")

            # Sync slash commands
            try:
                synced = await self.bot.tree.sync()
                logger.info(f"Synced {len(synced)} slash command(s)")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}")

        @self.bot.event
        async def on_message(message: discord.Message):
            # Ignore bot's own messages
            if message.author == self.bot.user:
                return

            # Check if we should respond
            should_respond = False

            # DM check
            if isinstance(message.channel, discord.DMChannel):
                if self.config.respond_to_dms:
                    should_respond = True
            else:
                # Server message - check for mention or prefix
                if (
                    self.config.respond_to_mentions and self.bot.user in message.mentions
                ) or message.content.startswith(self.config.command_prefix):
                    should_respond = True

            if not should_respond:
                return

            # Check permissions
            if not self._check_permissions(message):
                return

            # Extract message content (remove mention/prefix)
            content = message.content
            if self.bot.user.mentioned_in(message):
                content = content.replace(f"<@{self.bot.user.id}>", "").strip()
                content = content.replace(f"<@!{self.bot.user.id}>", "").strip()
            elif content.startswith(self.config.command_prefix):
                content = content[len(self.config.command_prefix) :].strip()

            if not content:
                return

            # Build metadata
            metadata = self._build_metadata(message)

            # Show typing indicator
            async with message.channel.typing():
                try:
                    # Route to handler
                    response = await self.message_handler(
                        "discord",
                        str(message.author.id),
                        content,
                        metadata,
                    )

                    # Send response (split if needed)
                    await self._send_response(message.channel, response)

                except Exception as e:
                    logger.error(f"Error handling Discord message: {e}")
                    await message.channel.send(
                        "❌ Sorry, I encountered an error processing your request."
                    )

            # Process commands
            await self.bot.process_commands(message)

    def _setup_commands(self):
        """Set up slash commands."""

        @self.bot.tree.command(name="navig", description="Send a command to NAVIG")
        @app_commands.describe(message="Your message or command")
        async def navig_command(interaction: discord.Interaction, message: str):
            # Check permissions
            if not self._check_user_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ You don't have permission to use this bot.",
                    ephemeral=True,
                )
                return

            if not self._check_channel_permission(interaction.channel_id):
                await interaction.response.send_message(
                    "❌ This channel is not authorized for NAVIG.",
                    ephemeral=True,
                )
                return

            # Defer for longer processing
            await interaction.response.defer(thinking=True)

            try:
                # Build metadata
                metadata = {
                    "guild_id": (str(interaction.guild_id) if interaction.guild_id else None),
                    "channel_id": str(interaction.channel_id),
                    "interaction": True,
                }

                # Route to handler
                response = await self.message_handler(
                    "discord",
                    str(interaction.user.id),
                    message,
                    metadata,
                )

                # Send followup
                await self._send_interaction_response(interaction, response)

            except Exception as e:
                logger.error(f"Error handling slash command: {e}")
                await interaction.followup.send(
                    "❌ Sorry, I encountered an error processing your request."
                )

        @self.bot.tree.command(name="status", description="Check NAVIG status")
        async def status_command(interaction: discord.Interaction):
            await interaction.response.send_message(
                "✅ **NAVIG is online and ready!**\n\n"
                "Use `/navig <command>` or mention me with your request.",
                ephemeral=True,
            )

        @self.bot.tree.command(name="help", description="Get NAVIG help")
        async def help_command(interaction: discord.Interaction):
            help_text = """
**🤖 NAVIG - Server Operations Assistant**

**How to use:**
• `/navig <message>` - Send a command
• `@NAVIG <message>` - Mention me with your request
• `!navig <message>` - Use the prefix command

**Example commands:**
• "Check disk space on production"
• "List docker containers"
• "Show database tables"
• "Price of bitcoin"
• "Search the web for Python tutorials"

**More info:** https://github.com/navig-run/core
            """
            await interaction.response.send_message(help_text.strip(), ephemeral=True)

    def _check_permissions(self, message: discord.Message) -> bool:
        """Check if message should be processed."""
        # Check user permission
        if not self._check_user_permission(message.author.id):
            return False

        # Check guild permission (for server messages)
        if message.guild and self.config.allowed_guilds:
            if message.guild.id not in self.config.allowed_guilds:
                return False

        # Check channel permission
        if not isinstance(message.channel, discord.DMChannel):
            if not self._check_channel_permission(message.channel.id):
                return False

        return True

    def _check_user_permission(self, user_id: int) -> bool:
        """Check if user is allowed."""
        if self.config.allowed_users is None:
            return True
        return user_id in self.config.allowed_users

    def _check_channel_permission(self, channel_id: int) -> bool:
        """Check if channel is allowed."""
        if self.config.allowed_channels is None:
            return True
        return channel_id in self.config.allowed_channels

    def _build_metadata(self, message: discord.Message) -> dict[str, Any]:
        """Build metadata from Discord message."""
        metadata = {
            "channel_id": str(message.channel.id),
            "message_id": str(message.id),
            "author_name": str(message.author),
            "timestamp": message.created_at.isoformat(),
        }

        if message.guild:
            metadata["guild_id"] = str(message.guild.id)
            metadata["guild_name"] = message.guild.name
            metadata["group_id"] = str(message.channel.id)  # For session routing

        return metadata

    async def _send_response(
        self,
        channel: discord.abc.Messageable,
        response: str,
    ):
        """Send response, splitting if needed."""
        # Discord message limit is 2000 chars
        max_length = 1900  # Leave some room

        if len(response) <= max_length:
            await channel.send(response)
            return

        # Split response
        chunks = []
        current = ""

        for line in response.split("\n"):
            if len(current) + len(line) + 1 > max_length:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line

        if current:
            chunks.append(current)

        # Send chunks
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(0.5)  # Rate limiting
            await channel.send(chunk)

    async def _send_interaction_response(
        self,
        interaction: discord.Interaction,
        response: str,
    ):
        """Send interaction followup response."""
        max_length = 1900

        if len(response) <= max_length:
            await interaction.followup.send(response)
            return

        # Split and send
        chunks = []
        current = ""

        for line in response.split("\n"):
            if len(current) + len(line) + 1 > max_length:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line

        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks):
            await interaction.followup.send(chunk)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)

    async def start(self):
        """Start the Discord bot."""
        if not self.config.token:
            raise ValueError(
                "Discord bot token not configured. "
                "Set DISCORD_BOT_TOKEN environment variable or provide token in config."
            )

        self._running = True
        logger.info("Starting Discord channel...")

        try:
            await self.bot.start(self.config.token)
        except Exception as e:
            logger.error(f"Discord bot error: {e}")
            self._running = False
            raise

    async def stop(self):
        """Stop the Discord bot."""
        if self._running:
            logger.info("Stopping Discord channel...")
            await self.bot.close()
            self._running = False

    @property
    def is_running(self) -> bool:
        """Check if bot is running."""
        return self._running and self.bot.is_ready()


def is_discord_available() -> bool:
    """Check if Discord integration is available."""
    return DISCORD_AVAILABLE


def create_discord_channel(
    config: DiscordChannelConfig | None = None,
    message_handler: Callable | None = None,
) -> DiscordChannel:
    """
    Create a Discord channel adapter.

    Args:
        config: Discord configuration (uses defaults if not provided)
        message_handler: Message routing callback

    Returns:
        Configured DiscordChannel instance
    """
    if config is None:
        config = DiscordChannelConfig()

    if message_handler is None:
        # Default handler that echoes
        async def echo_handler(channel, user_id, message, metadata):
            return f"Echo: {message}"

        message_handler = echo_handler

    return DiscordChannel(config, message_handler)
