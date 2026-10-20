"""The gateway's Discord bot: when it starts, and how its slash commands are scoped."""

from __future__ import annotations

import pytest

from navig.discord import settings as settings_mod
from navig.gateway.channels import discord as channel_mod


def test_not_started_when_disabled(monkeypatch):
    monkeypatch.setattr(settings_mod, "load", lambda: settings_mod.DiscordSettings(enabled=False))
    assert channel_mod.create_discord_channel(None) is None


def test_not_started_without_a_token(monkeypatch):
    import navig.discord.config as cfg

    monkeypatch.setattr(settings_mod, "load", lambda: settings_mod.DiscordSettings(enabled=True))
    monkeypatch.setattr(cfg, "get_token", lambda identity: "")
    assert channel_mod.create_discord_channel(None) is None


def test_not_started_without_discord_py(monkeypatch):
    import navig.discord.config as cfg

    monkeypatch.setattr(settings_mod, "load", lambda: settings_mod.DiscordSettings(enabled=True))
    monkeypatch.setattr(cfg, "get_token", lambda identity: "tok")
    monkeypatch.setattr(channel_mod, "DISCORD_AVAILABLE", False)
    assert channel_mod.create_discord_channel(None) is None


def test_chunks_respect_discords_limit():
    parts = channel_mod._chunks("x" * 4500)
    assert [len(p) for p in parts] == [2000, 2000, 500]
    assert channel_mod._chunks("") == ["(no response)"]


@pytest.fixture
def bot():
    pytest.importorskip("discord.app_commands")
    s = settings_mod.DiscordSettings(enabled=True, owners=frozenset({"1"}))
    return channel_mod.DiscordChannel("tok", s, None)


def test_bot_uses_no_privileged_intents(bot):
    intents = bot.client.intents
    assert not intents.message_content and not intents.members and not intents.presences


def test_moderation_commands_are_guild_only_and_permission_gated(bot):
    import discord

    cmds = {c.name: c for c in bot.tree.get_commands()}
    assert {"navig", "status", "help", "kick", "ban", "unban", "timeout", "untimeout", "role"} <= set(cmds)
    expected = {"kick": "kick_members", "ban": "ban_members", "unban": "ban_members",
                "timeout": "moderate_members", "untimeout": "moderate_members", "role": "manage_roles"}
    for name, perm in expected.items():
        cmd = cmds[name]
        assert cmd.guild_only, name
        assert isinstance(cmd.default_permissions, discord.Permissions), name
        assert getattr(cmd.default_permissions, perm), name
    assert {c.name for c in cmds["role"].commands} == {"add", "remove", "create", "delete"}
    # the agent command is NOT permission-hidden — it refuses non-owners at runtime instead
    assert cmds["navig"].default_permissions is None


def test_moderation_can_be_turned_off(monkeypatch):
    pytest.importorskip("discord.app_commands")
    s = settings_mod.DiscordSettings(enabled=True, moderation_enabled=False)
    names = {c.name for c in channel_mod.DiscordChannel("tok", s, None).tree.get_commands()}
    assert names == {"navig", "status", "help"}
