"""The bot's access decisions: fail-closed agent, server allowlist, cooldown, replies."""

from __future__ import annotations

import pytest

from navig.discord import bot_logic, modlog
from navig.discord.settings import DiscordSettings, _ids

from .fake_discord import ADMIN, MEMBER, MOD, FakeDiscord, G


@pytest.fixture(autouse=True)
def _modlog_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(modlog, "path", lambda: tmp_path / "modlog.jsonl")


def test_agent_is_closed_when_no_owner_is_configured():
    assert not bot_logic.agent_allowed(DiscordSettings(), 123, None)


def test_agent_answers_owners_only():
    s = DiscordSettings(owners=frozenset({"123"}))
    assert bot_logic.agent_allowed(s, 123, None)
    assert bot_logic.agent_allowed(s, "123", 42)
    assert not bot_logic.agent_allowed(s, 456, None)
    assert not bot_logic.agent_allowed(DiscordSettings(owners=frozenset({"123"}), agent_enabled=False), 123, None)


def test_agent_respects_the_server_allowlist():
    s = DiscordSettings(owners=frozenset({"123"}), allowed_guilds=frozenset({"42"}))
    assert bot_logic.agent_allowed(s, 123, 42)
    assert not bot_logic.agent_allowed(s, 123, 43)
    assert bot_logic.agent_allowed(s, 123, None)  # DMs are not a server


def test_should_leave_only_when_an_allowlist_exists():
    assert not bot_logic.should_leave(DiscordSettings(), 42)
    s = DiscordSettings(allowed_guilds=frozenset({"42"}))
    assert not bot_logic.should_leave(s, 42)
    assert bot_logic.should_leave(s, 43)


def test_cooldown_rolls_over_a_minute():
    now = [0.0]
    cd = bot_logic.Cooldown(2, clock=lambda: now[0])
    assert cd.hit("a") == 0 and cd.hit("a") == 0
    assert cd.hit("a") == pytest.approx(60)
    assert cd.hit("b") == 0  # per key
    now[0] = 60.0
    assert cd.hit("a") == 0


def test_settings_lists_accept_strings_and_drop_garbage():
    assert _ids("123, 456,abc,,") == frozenset({"123", "456"})
    assert _ids([123, "456"]) == frozenset({"123", "456"})
    assert _ids(None) == frozenset()


def test_settings_load_coerces_string_booleans(monkeypatch):
    from navig.discord import settings

    values = {"discord.enabled": "false", "discord.moderation.confirm": "off",
              "discord.owners": "1,2", "discord.sync": "bogus", "discord.moderation.cooldown": "x"}

    class Cfg:
        def get(self, key, default=None):
            return values.get(key, default)

    monkeypatch.setattr(settings, "_cfg", lambda: Cfg())
    s = settings.load()
    assert s.enabled is False and s.confirm is False
    assert s.owners == frozenset({"1", "2"})
    assert s.sync == "guild" and s.cooldown_per_minute == 5


def test_moderate_replies_with_a_refusal_not_an_exception():
    fake = FakeDiscord()
    reply = bot_logic.moderate(fake.client, "kick", guild_id=G, invoker_id=MOD, invoker_name="mod",
                               target_id=ADMIN, reason="x")
    assert reply.startswith("⛔")
    assert fake.writes == []


def test_moderate_kicks_and_tags_the_invoker_in_the_audit_log():
    from urllib.parse import unquote

    fake = FakeDiscord()
    reply = bot_logic.moderate(fake.client, "kick", guild_id=G, invoker_id=MOD, invoker_name="mod#1",
                               target_id=MEMBER, reason="spam")
    assert reply == f"✅ Kicked <@{MEMBER}>."
    assert unquote(fake.writes[-1][3]) == f"mod#1 ({MOD}) via NAVIG: spam"
    assert modlog.tail(1)[0]["surface"] == "discord"


def test_moderate_bad_duration_is_a_readable_warning():
    reply = bot_logic.moderate(FakeDiscord().client, "timeout", guild_id=G, invoker_id=MOD,
                               invoker_name="mod", target_id=MEMBER, duration="forever")
    assert reply.startswith("⚠ not a duration")
