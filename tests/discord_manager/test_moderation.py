"""The moderation policy + service: who may do what to whom, enforced before any write."""

from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

from navig.discord import moderation, modlog, policy
from navig.discord.policy import Actor, PolicyDenied

from .fake_discord import ADMIN, BOT, MEMBER, MOD, OWNER, STRANGER, FakeDiscord, G


@pytest.fixture(autouse=True)
def _modlog_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(modlog, "path", lambda: tmp_path / "modlog.jsonl")


@pytest.fixture
def fake():
    return FakeDiscord()


def _actor(fake, client, user_id):
    ctx = moderation.context(client, G)
    return ctx, ctx.actor_for(client.member(G, user_id))


# ── policy, pure ─────────────────────────────────────────────────────────────

BOT_A = Actor(BOT, 4, policy.KICK_MEMBERS | policy.MANAGE_ROLES)


def test_nobody_moderates_the_owner_even_an_admin():
    admin = Actor(ADMIN, 5, policy.ADMINISTRATOR)
    with pytest.raises(PolicyDenied, match="owner"):
        policy.check_member_action("kick", actor=admin, bot=BOT_A, target_id=OWNER,
                                   target_top_position=0, guild_owner_id=OWNER)


def test_equal_rank_is_refused():
    mod = Actor(MOD, 3, policy.KICK_MEMBERS)
    with pytest.raises(PolicyDenied, match="Your highest role"):
        policy.check_member_action("kick", actor=mod, bot=BOT_A, target_id="7",
                                   target_top_position=3, guild_owner_id=OWNER)


def test_owner_outranks_everyone_but_the_bot_still_must():
    owner = Actor(OWNER, 0, 0, is_owner=True)
    with pytest.raises(PolicyDenied, match="bot's highest role"):
        policy.check_member_action("kick", actor=owner, bot=BOT_A, target_id=ADMIN,
                                   target_top_position=5, guild_owner_id=OWNER)


def test_missing_permission_names_it():
    with pytest.raises(PolicyDenied, match="Ban Members"):
        policy.check_member_action("ban", actor=Actor(MOD, 3, policy.KICK_MEMBERS), bot=Actor(BOT, 4, 0xFFFF),
                                   target_id=MEMBER, target_top_position=1, guild_owner_id=OWNER)


def test_cannot_act_on_self_or_bot():
    mod = Actor(MOD, 3, policy.KICK_MEMBERS)
    for target in (MOD, BOT):
        with pytest.raises(PolicyDenied):
            policy.check_member_action("kick", actor=mod, bot=BOT_A, target_id=target,
                                       target_top_position=0, guild_owner_id=OWNER)


def test_role_escalation_is_blocked():
    mod = Actor(MOD, 3, policy.MANAGE_ROLES | policy.KICK_MEMBERS)
    with pytest.raises(PolicyDenied, match="Only the server owner or an Administrator"):
        policy.check_role_action("role_create", actor=mod, bot=Actor(BOT, 4, policy.ADMINISTRATOR),
                                 role_position=None, new_permissions=policy.ADMINISTRATOR)
    with pytest.raises(PolicyDenied, match="don't have"):
        policy.check_role_action("role_create", actor=mod, bot=Actor(BOT, 4, policy.ADMINISTRATOR),
                                 role_position=None, new_permissions=policy.PERMISSIONS["manage_messages"])
    # what the mod holds and is not dangerous: allowed
    policy.check_role_action("role_create", actor=mod, bot=Actor(BOT, 4, policy.ADMINISTRATOR),
                             role_position=None, new_permissions=policy.KICK_MEMBERS)


def test_parsers():
    assert policy.parse_permissions("kick_members, ban_members") == policy.KICK_MEMBERS | policy.BAN_MEMBERS
    assert policy.parse_permissions("8") == 8
    with pytest.raises(ValueError, match="unknown permission"):
        policy.parse_permissions("fly")
    assert policy.parse_color("#ff8800") == 0xFF8800
    assert policy.parse_duration("10m") == 600
    assert policy.parse_duration("28d") == policy.MAX_TIMEOUT_SECONDS
    for bad in ("", "10", "0m", "29d", "5y"):
        with pytest.raises(ValueError):
            policy.parse_duration(bad)


def test_audit_reason_is_one_line_and_bounded():
    text = policy.audit_reason("spam\n@everyone\r\nagain " + "x" * 600, actor_name="mod", actor_id=MOD)
    assert "\n" not in text and "\r" not in text
    assert text.startswith(f"mod ({MOD}) via NAVIG: spam @everyone again")
    assert len(text) == 512


# ── service, against a fake server ───────────────────────────────────────────


def test_cli_kick_member_sends_audited_delete_and_logs(fake):
    with fake.client() as c:
        moderation.member_action(c, "kick", G, MEMBER, reason="spam")
    method, path, _, reason = fake.writes[-1]
    assert (method, path) == ("DELETE", f"/guilds/{G}/members/{MEMBER}")
    assert unquote(reason) == "operator via NAVIG: spam"
    entry = modlog.tail(1)[0]
    assert entry["action"] == "kick" and entry["ok"] and entry["target_id"] == MEMBER


def test_mod_cannot_kick_admin_and_nothing_is_sent(fake):
    with fake.client() as c:
        ctx, mod = _actor(fake, c, MOD)
        with pytest.raises(PolicyDenied):
            moderation.member_action(c, "kick", G, ADMIN, actor=mod, ctx=ctx)
    assert fake.writes == []


def test_bot_below_target_is_refused_even_for_cli(fake):
    with fake.client() as c, pytest.raises(PolicyDenied, match="bot's highest role"):
        moderation.member_action(c, "kick", G, ADMIN)
    assert fake.writes == []


def test_ban_a_user_who_already_left(fake):
    with fake.client() as c:
        ctx, mod = _actor(fake, c, MOD)
        moderation.member_action(c, "ban", G, STRANGER, actor=mod, actor_name="mod", ctx=ctx,
                                 delete_message_seconds=3600)
    method, path, body, _ = fake.writes[-1]
    assert (method, path, body) == ("PUT", f"/guilds/{G}/bans/{STRANGER}", {"delete_message_seconds": 3600})


def test_ban_always_passes_delete_seconds_so_discord_does_not_purge_a_day(fake):
    with fake.client() as c:
        moderation.member_action(c, "ban", G, MEMBER)
    assert fake.writes[-1][2] == {"delete_message_seconds": 0}


def test_timeout_sets_an_iso_deadline_and_untimeout_clears_it(fake):
    with fake.client() as c:
        moderation.member_action(c, "timeout", G, MEMBER, duration_seconds=600)
        moderation.member_action(c, "untimeout", G, MEMBER)
    first, second = fake.writes[-2:]
    assert first[0] == "PATCH" and first[2]["communication_disabled_until"].endswith("+00:00")
    assert second[2] == {"communication_disabled_until": None}


def test_admin_cannot_be_timed_out(fake):
    with fake.client() as c:
        ctx, owner = _actor(fake, c, OWNER)
        with pytest.raises(PolicyDenied, match="Administrator"):
            moderation.member_action(c, "timeout", G, ADMIN, actor=owner, ctx=ctx, duration_seconds=60)


def test_failed_action_is_logged_as_failed(fake, monkeypatch):
    from navig.discord.api import DiscordAPIError

    with fake.client() as c:
        monkeypatch.setattr(c, "kick", lambda *a, **k: (_ for _ in ()).throw(DiscordAPIError(500, "boom")))
        with pytest.raises(DiscordAPIError):
            moderation.member_action(c, "kick", G, MEMBER)
    entry = modlog.tail(1)[0]
    assert entry["ok"] is False and "boom" in entry["detail"]


def test_role_assign_respects_both_hierarchies(fake):
    with fake.client() as c:
        ctx, mod = _actor(fake, c, MOD)
        # Mod can hand out Member (pos 1) to a member...
        moderation.role_assign(c, G, MEMBER, "Member", add=True, actor=mod, ctx=ctx)
        # ...but not their own Mod role (pos 3 == their top) nor Admin.
        for role in ("Mod", "Admin"):
            with pytest.raises(PolicyDenied):
                moderation.role_assign(c, G, MEMBER, role, add=True, actor=mod, ctx=ctx)
        # and a managed (bot) role is never assignable by hand
        with pytest.raises(PolicyDenied, match="managed"):
            moderation.role_assign(c, G, MEMBER, "NAVIG", add=True, ctx=ctx)
    assert [w[1] for w in fake.writes] == [f"/guilds/{G}/members/{MEMBER}/roles/12"]


def test_role_create_and_delete(fake):
    with fake.client() as c:
        created = moderation.role_create(c, G, "Helpers", permissions=policy.KICK_MEMBERS, color=0x00FF00)
        moderation.role_delete(c, G, "Member")
        with pytest.raises(PolicyDenied, match="@everyone"):
            moderation.role_delete(c, G, G)
    assert created["name"] == "Helpers"
    post = next(w for w in fake.writes if w[0] == "POST")
    assert json.loads(json.dumps(post[2])) == {"name": "Helpers", "permissions": str(policy.KICK_MEMBERS),
                                               "hoist": False, "mentionable": False, "color": 0x00FF00}


def test_role_edit_cannot_raise_a_role_above_what_the_mod_holds(fake):
    with fake.client() as c:
        ctx, mod = _actor(fake, c, MOD)
        with pytest.raises(PolicyDenied):
            moderation.role_edit(c, G, "Member", {"permissions": policy.ADMINISTRATOR}, actor=mod, ctx=ctx)
        updated = moderation.role_edit(c, G, "Member", {"name": "Members"}, actor=mod, ctx=ctx)
    assert updated["name"] == "Members"


def test_unknown_role_name_is_a_lookup_error(fake):
    with fake.client() as c, pytest.raises(LookupError):
        moderation.role_delete(c, G, "Nope")
