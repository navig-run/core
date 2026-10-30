"""Tests for navig.telegram.autoreply — business "pro mode" persona auto-reply.

Owner-only activation, command deletion, counterparty auto-reply with human-like
sending, and the owner/inactive no-op guards.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.telegram.autoreply as ar


async def _drain_autoreply_tasks() -> None:
    """Await the detached background auto-reply task(s) maybe_autoreply spawns."""
    await asyncio.gather(*list(ar._bg_tasks))


def _channel():
    ch = MagicMock()
    ch._api_call = AsyncMock(return_value={"ok": True, "message_id": 1})
    ch.send_message = AsyncMock()
    return ch


# ── command parsing ──────────────────────────────────────────────────────────


def test_parse_command_forms():
    assert ar.parse_command("role tyler fr on") == {"toggle": True, "role": "tyler", "lang": "fr"}
    assert ar.parse_command("role support on")["role"] == "support"
    assert ar.parse_command("role tyler fr")["toggle"] is True  # naming implies ON
    assert ar.parse_command("role off")["toggle"] is False
    assert ar.parse_command("role")["toggle"] == "status"
    assert ar.parse_command("hello there") is None


# ── owner control ────────────────────────────────────────────────────────────


async def test_handle_command_activates_and_deletes(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(ar, "_set_active",
                        lambda cid, role, lang: state.__setitem__(str(cid), {"role": role, "lang": lang}))
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 10, "business_connection_id": "bc1",
           "text": "role tyler fr on"}

    handled = await ar.handle_command(ch, msg, is_owner=True, owner_id=777)
    assert handled is True
    # The control message is deleted from the business chat.
    assert any(c.args[0] == "deleteBusinessMessages" for c in ch._api_call.call_args_list)
    # Confirmation goes to the OWNER privately (777), never into the business chat.
    assert ch.send_message.call_args.args[0] == 777
    assert state["555"] == {"role": "tyler", "lang": "fr"}


async def test_handle_command_non_owner_is_noop():
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 10, "text": "role tyler fr on"}
    assert await ar.handle_command(ch, msg, is_owner=False, owner_id=777) is False
    ch.send_message.assert_not_called()


# ── counterparty auto-reply ──────────────────────────────────────────────────


async def test_maybe_autoreply_generates_and_sends(monkeypatch):
    monkeypatch.setattr(ar.asyncio, "sleep", AsyncMock())  # skip the typing delays
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "sales", "lang": "fr"})
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [("Them", "Bonjour")])

    async def fake_generate(role, lang, ctx, *, target=None, **_):
        assert role == "sales" and lang == "fr"
        assert target == "Bonjour", "the message being answered reaches the model"
        return "Bonjour ! Comment puis-je vous aider ?"

    monkeypatch.setattr(ar, "_generate", fake_generate)
    ch = _channel()
    msg = {"chat": {"id": 555}, "message_id": 11, "business_connection_id": "bc1", "text": "Bonjour"}

    replied = await ar.maybe_autoreply(ch, msg, is_owner=False, owner_id=777)
    assert replied is True
    # The reply is DETACHED (so the ~34s human-like delay can't block update dispatch):
    # nothing is sent until the background task runs.
    assert not ch._api_call.call_args_list, "the send must not happen inline in maybe_autoreply"
    await _drain_autoreply_tasks()
    # showed a typing indicator and sent AS the business account
    assert any(c.args[0] == "sendChatAction" for c in ch._api_call.call_args_list)
    sent = [c for c in ch._api_call.call_args_list if c.args[0] == "sendMessage"]
    assert sent and sent[0].args[1]["business_connection_id"] == "bc1"
    assert "Bonjour" in sent[0].args[1]["text"]


async def test_maybe_autoreply_never_replies_to_owner(monkeypatch):
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "sales", "lang": ""})
    ch = _channel()
    msg = {"chat": {"id": 555}, "text": "note to self"}
    assert await ar.maybe_autoreply(ch, msg, is_owner=True, owner_id=777) is False


async def test_maybe_autoreply_inactive_is_noop(monkeypatch):
    monkeypatch.setattr(ar, "get_active", lambda cid: None)
    ch = _channel()
    msg = {"chat": {"id": 555}, "text": "hi"}
    assert await ar.maybe_autoreply(ch, msg, is_owner=False, owner_id=777) is False


async def test_a_burst_from_one_chat_coalesces_into_one_more_reply(monkeypatch):
    """Five quick messages must not start five LLM calls (and five typing delays)."""
    yield_now = asyncio.sleep
    monkeypatch.setattr(ar.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "sales", "lang": ""})
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    gate = asyncio.Event()
    targets: list[str] = []

    async def slow_generate(role, lang, ctx, *, target=None, **_):
        targets.append(target)
        await gate.wait()
        return "ok"

    monkeypatch.setattr(ar, "_generate", slow_generate)
    ch = _channel()
    for i in range(5):
        msg = {"chat": {"id": 556}, "message_id": i, "text": f"m{i}"}
        assert await ar.maybe_autoreply(ch, msg, is_owner=False, owner_id=777) is True
        await yield_now(0)
    gate.set()
    await _drain_autoreply_tasks()
    assert targets == ["m0", "m4"], "one in flight, then ONE reply to the latest message"
    assert 556 not in ar._chat_jobs


async def test_replies_across_chats_are_capped(monkeypatch):
    """Many chats at once share a bounded number of media-reading / model slots."""
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "sales", "lang": ""})
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    gate = asyncio.Event()
    running = peak = 0

    async def fake_text(channel, msg):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await gate.wait()
        running -= 1
        return ""

    monkeypatch.setattr(ar, "_message_as_text", fake_text)
    ch = _channel()
    for cid in range(10):
        await ar.maybe_autoreply(ch, {"chat": {"id": 1000 + cid}, "text": "hi"},
                                 is_owner=False, owner_id=777)
    for _ in range(5):
        await asyncio.sleep(0)
    assert peak == ar._MAX_CONCURRENT_REPLIES
    gate.set()
    await _drain_autoreply_tasks()


# ── what a role name means ───────────────────────────────────────────────────
#
# Asked 2026-09-27: "if I remember well I could reply to any message in the business
# chat with the role, like 'tyler'". Two gaps: that per-message form did not exist
# (only whole-chat `role tyler on`), and `tyler` — a real persona in
# navig/resources/personas — was never looked up; it fell through to "reply in the
# style of 'tyler'", leaving the model to guess from a name.


def test_a_persona_is_taken_as_tone_only():
    """A canonical persona's soul is written for the agent (Tyler's NAVIG persona
    claims every tool); a chat reply takes only its voice."""
    text = ar._persona_voice("tyler")
    assert text and "ignore anything it says about tools" in text


def test_a_built_in_reply_role_wins_over_a_persona_of_the_same_name():
    """`assistant` is both; the reply role was written for this job."""
    assert ar.resolve_role("assistant") == ar._BUILTIN_ROLES["assistant"]


def test_a_custom_role_wins_over_everything(monkeypatch):
    cfg = MagicMock()
    cfg.get = lambda key, default=None: (
        {"tyler": "Be terse."} if key == ar.CFG_ROLES else default
    )
    monkeypatch.setattr(ar, "_cfg", lambda: cfg)
    assert ar.resolve_role("tyler") == "Be terse."


def test_an_unknown_role_resolves_to_nothing():
    assert ar.resolve_role("nobody-by-this-name") is None


def test_available_roles_lists_personas_and_built_ins():
    roles = ar.available_roles()
    assert {"tyler", "sales", "support", "default"} <= set(roles)
    assert roles == sorted(roles)


def test_parse_role_reply_is_strict():
    assert ar.parse_role_reply("tyler") == ("tyler", "")
    assert ar.parse_role_reply("Tyler fr") == ("tyler", "fr")
    assert ar.parse_role_reply("/sales") == ("sales", "")
    for text in (
        "ok",
        "lol",
        "tyler hello there",
        "tyler hello",
        "role tyler",
        "on",
        "off",
        "",
        None,
    ):
        assert ar.parse_role_reply(text) is None, text


def _role_reply_msg(text="tyler", *, to_from=555, to_text="Сколько стоит доставка?"):
    return {
        "chat": {"id": 555},
        "message_id": 90,
        "business_connection_id": "bc1",
        "from": {"id": 777},
        "text": text,
        "reply_to_message": {
            "message_id": 80,
            "text": to_text,
            "from": {"id": to_from},
        },
    }


async def test_a_role_reply_does_not_switch_pro_mode_on(monkeypatch):
    calls = []
    monkeypatch.setattr(ar, "_set_active", lambda *a: calls.append(a))
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value=""))
    await ar.maybe_role_reply(
        _channel(), _role_reply_msg(), is_owner=True, owner_id=777
    )
    await _drain_autoreply_tasks()
    assert calls == []


async def test_an_ordinary_one_word_reply_is_left_alone():
    ch = _channel()
    assert (
        await ar.maybe_role_reply(
            ch, _role_reply_msg("ok"), is_owner=True, owner_id=777
        )
        is False
    )
    assert not ch._api_call.called, "nothing deleted, nothing sent"


async def test_only_the_owner_and_only_a_reply():
    ch = _channel()
    assert (
        await ar.maybe_role_reply(ch, _role_reply_msg(), is_owner=False, owner_id=777)
        is False
    )
    msg = _role_reply_msg()
    msg.pop("reply_to_message")
    assert await ar.maybe_role_reply(ch, msg, is_owner=True, owner_id=777) is False
    assert not ch._api_call.called


async def test_a_failed_generation_tells_the_owner(monkeypatch):
    """The trigger is already deleted; without a note it just vanishes."""
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value=""))
    ch = _channel()
    await ar.maybe_role_reply(ch, _role_reply_msg(), is_owner=True, owner_id=777)
    await _drain_autoreply_tasks()
    assert not [c for c in ch._api_call.call_args_list if c.args[0] == "sendMessage"]
    note = ch.send_message.await_args
    assert note.args[0] == 777 and "tyler" in note.args[1]


async def test_the_generated_prompt_carries_tylers_voice_and_the_message(monkeypatch):
    """End to end through the real `_generate`: the persona text and the message
    being answered both reach the model."""
    captured = {}

    def fake_llm(messages, **_):
        captured["system"], captured["user"] = (
            messages[0]["content"],
            messages[1]["content"],
        )
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    out = await ar._generate(
        "tyler", "", [("Them", "hi")], target="Сколько стоит доставка?"
    )
    assert out == "ok"
    assert "You are Tyler Durden from Fight Club" in captured["system"]
    assert "Сколько стоит доставка?" in captured["user"]


def test_role_status_lists_the_roles_and_both_usages(monkeypatch):
    monkeypatch.setattr(ar, "get_active", lambda cid: None)
    text = ar._status_text(555)
    assert "<code>tyler</code>" in text
    assert "Reply to one of their messages" in text and "role off" in text


# ── a role on your OWN message ───────────────────────────────────────────────
#
# Asked 2026-09-29, after this used to refuse ("that one is yours"): "but I can reply
# to my own too, why can't I?" There is nothing of theirs to answer, so on your own
# message the role means "say this the way Tyler would". Send first, remove second:
# the original goes only once Telegram has accepted the rewrite.


def _own_msg(*, parent=None, text="завтра в 6, не опаздывай"):
    target = {"message_id": 80, "text": text, "from": {"id": 777}}
    if parent is not None:
        target["reply_to_message"] = {"message_id": parent}
    return {
        "chat": {"id": 555},
        "message_id": 90,
        "business_connection_id": "bc1",
        "from": {"id": 777},
        "text": "tyler",
        "reply_to_message": target,
    }


def _calls(ch, method):
    return [c.args[1] for c in ch._api_call.call_args_list if c.args[0] == method]


async def test_a_failed_rewrite_keeps_the_original_and_says_so(monkeypatch):
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value=""))
    ch = _channel()
    await ar.maybe_role_reply(ch, _own_msg(), is_owner=True, owner_id=777)
    await _drain_autoreply_tasks()
    assert not _calls(ch, "sendMessage")
    deleted = [
        ids for d in _calls(ch, "deleteBusinessMessages") for ids in d["message_ids"]
    ]
    assert deleted == [90], "only the trigger — never the original"
    assert "left as it was" in ch.send_message.await_args.args[1]


async def test_the_rewrite_prompt_keeps_meaning_and_language(monkeypatch):
    captured = {}

    def fake_llm(messages, **_):
        captured["system"], captured["user"] = (
            messages[0]["content"],
            messages[1]["content"],
        )
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", "", [], target="завтра в 6", rewrite=True)
    assert "Rewrite that message in your voice" in captured["user"]
    assert "Keep ALL of it" in captured["user"]
    assert "no added facts, promises or details" in captured["user"]
    assert "Keep the language the owner's message is written in" in captured["system"]


async def test_the_send_reports_a_refusal():
    """`_api_call` says no by returning None; the rewrite's safety rests on this."""
    ch = _channel()
    ch._api_call = AsyncMock(return_value=None)
    assert await ar._send(ch, 555, "bc1", "x") is False
    ch._api_call = AsyncMock(return_value={"message_id": 5})
    assert await ar._send(ch, 555, "bc1", "x") is True


# ── `tyler` is Tyler Durden ──────────────────────────────────────────────────
#
# Reported 2026-09-29: "tyler answers very soft, not like Tyler Durden — it's Tyler
# Durden from Fight Club". Two causes, both measured: `tyler` resolved to NAVIG's own
# assistant persona ("Direct. Sharp. Done."), and replies were written by the `chat`
# mode, a 3B local model on this router. The live reply was "Здравствуйте, какое
# удовольствие вас слышать! На выходных всегда готов помочь…".


@pytest.mark.parametrize("name", ["tyler", "durden", "tyler-durden", "Tyler"])
def test_tyler_is_tyler_durden_from_fight_club(name):
    text = ar.resolve_role(name)
    assert "You are Tyler Durden from Fight Club" in text
    assert "full access to every tool" not in text, (
        "NAVIG's assistant persona is not him"
    )


def test_he_is_the_film_character_not_a_productivity_coach():
    """Reported 2026-09-29 after switching to grok: "punk" got two tame English lines
    about starting your own fight. The voice came from the shipped agent soul, a
    productivity coach wearing his name. The old bot the operator measures against
    ranted 5-8 raw sentences and closed on a line of its own, in quotes."""
    text = ar.resolve_role("tyler")
    assert "not a life coach" in text
    assert "Profanity" in text
    assert "4 to 8 sentences" in text
    assert "in quotes" in text
    assert "The plan is a weapon" not in text and "ship it" not in text.lower()


def test_no_word_list_or_example_line_to_parrot():
    """Measured: a word list came back as "Do it differently. Own it now. Ship
    something." and example lines were recited whenever a message resembled one."""
    text = ar.resolve_role("tyler")
    assert "Words he reaches for" not in text
    assert "do not reuse the film's famous lines" in text
    assert "The things you own end up owning you" not in text


def test_he_never_sounds_like_customer_service():
    assert "never customer service" in ar.resolve_role("tyler")


async def test_no_blanket_concise_rule_overrides_the_character(monkeypatch):
    """The shared "Keep it … appropriately concise" line beat the
    character's rant AND cut a 380-char rewrite down to one sentence."""
    captured = {}

    def fake_llm(messages, **_):
        captured["system"] = messages[0]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", "", [("Them", "punk")], target="punk")
    assert "concise" not in captured["system"]


def test_a_one_word_message_takes_the_chats_language():
    """"punk" alone reads as English; the chat around it was French."""
    ctx = [("Them", "tu fais quoi ce soir, on sort ?"), ("Me", "je bosse")]
    sample = ar._language_sample("punk", ctx, rewrite=False)
    assert "tu fais quoi ce soir" in sample and sample.endswith("punk")
    assert "je bosse" not in sample, "their language, not the owner's"


def test_a_rewrite_takes_the_language_of_the_message_itself():
    ctx = [("Them", "hello there")]
    assert ar._language_sample("завтра в 6", ctx, rewrite=True) == "завтра в 6"


def test_the_language_sample_stays_short():
    ctx = [("Them", "mot " * 200)]
    assert len(ar._language_sample("punk", ctx, rewrite=False)) <= ar._LANG_SAMPLE_CHARS


async def test_the_prompt_names_the_language_sample(monkeypatch):
    captured = {}

    def fake_llm(messages, **_):
        captured["user"] = messages[1]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", "", [("Them", "on sort ce soir ?")], target="punk")
    assert "same language as this: «on sort ce soir ? punk»" in captured["user"]


def test_durden_is_listed_as_a_role():
    assert {"tyler", "durden", "tyler-durden"} <= set(ar.available_roles())


def test_replies_no_longer_use_the_small_chat_model(monkeypatch):
    cfg = MagicMock()
    cfg.get = lambda key, default=None: default
    monkeypatch.setattr(ar, "_cfg", lambda: cfg)
    choice = ar._model_choice()
    assert choice.get("mode") == "big", choice
    assert choice.get("temperature", 0) >= 0.8, (
        "a character needs room to sound like one"
    )


def test_a_configured_model_is_used_as_is(monkeypatch):
    cfg = MagicMock()
    cfg.get = lambda key, default=None: (
        "xai:grok-3-mini" if key == ar.CFG_MODEL else default
    )
    monkeypatch.setattr(ar, "_cfg", lambda: cfg)
    assert ar._model_choice()["model_override"] == "xai:grok-3-mini"


async def test_generation_uses_the_chosen_model_and_pins_the_language(monkeypatch):
    captured = {}

    def fake_llm(messages, **kw):
        captured.update(kw, user=messages[1]["content"])
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    monkeypatch.setattr(
        ar, "_model_choice", lambda: {"mode": "big", "temperature": 0.9}
    )
    await ar._generate("tyler", "", [], target="Сколько стоит доставка?")

    assert captured["mode"] == "big" and "chat" not in str(captured.get("mode"))
    assert captured["user"].rstrip().endswith("«Сколько стоит доставка?»)"), (
        "the language instruction must come LAST — a small model followed the "
        "English persona text instead"
    )


async def test_a_named_language_is_not_overridden(monkeypatch):
    captured = {}

    def fake_llm(messages, **kw):
        captured["user"] = messages[1]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", "fr", [], target="Сколько стоит доставка?")
    assert "same language as this" not in captured["user"]


async def test_the_output_rule_allows_tylers_quoted_closing_line(monkeypatch):
    """Tyler must close on an aphorism "in quotes"; a later blanket "no quotes" in the
    system prompt told the model to drop it. Only wrapping the WHOLE reply is barred."""
    captured = {}

    def fake_llm(messages, **_):
        captured["system"] = messages[0]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", "", [], target="hi")
    assert "in quotes" in captured["system"], "the persona still asks for the quoted line"
    assert "no quotes" not in captured["system"]
    assert "do not wrap the whole reply in quotes" in captured["system"]


async def test_the_floor_does_not_deny_a_professional_owners_credentials(monkeypatch):
    """The reply speaks AS the owner. The minimal floor's "you are not a doctor or a
    licensed adviser" then told a doctor's or lawyer's auto-reply that the owner is
    not one — the floor must protect without making claims about who the owner is."""
    captured = {}

    def fake_llm(messages, **_):
        captured["system"] = messages[0]["content"]
        return "ok"

    import navig.llm.generate as gen_mod
    from navig.agent.conv.guardrails import guardrail_floor_impersonation

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("support", "", [], target="Can I take it twice a day?")
    system = captured["system"]
    assert "not a doctor" not in system and "licensed adviser" not in system
    assert system.startswith(guardrail_floor_impersonation())
    assert "never fabricate" in system and "private data private" in system


async def test_a_bot_authored_echo_of_the_owner_is_rewritten_not_answered(monkeypatch):
    """Telegram can hand back a message this bot sent as the owner with `from` = the
    bot. Replying `tyler` to it must take the rewrite path (the owner's own words),
    not answer it as if the counterparty had written it."""
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value=""))
    ch = _channel()
    ch.bot_token = "4242:secret"
    msg = _own_msg()
    msg["reply_to_message"]["from"] = {"id": 4242, "is_bot": True}
    await ar.maybe_role_reply(ch, msg, is_owner=True, owner_id=777)
    await _drain_autoreply_tasks()
    note = ch.send_message.await_args.args[1]
    assert "your message was left as it was" in note, note


def test_a_captionless_photo_does_not_pin_its_english_description():
    """`[a photo showing: …]` is NAVIG's English; in a Russian chat the sample must be
    the chat's words, not the description."""
    photo = {"message_id": 80, "photo": [{"file_id": "x"}]}
    words = ar._language_words(photo, "[a photo showing: a red car]")
    assert words == ""
    sample = ar._language_sample("[a photo showing: a red car]",
                                 [("Them", "смотри какая машина")], False, words)
    assert sample == "смотри какая машина"


def test_spoken_and_written_media_words_still_carry_the_language():
    voice = {"message_id": 80, "voice": {"file_id": "x"}}
    assert ar._language_words(voice, "[a voice message saying: привет, как дела]") == (
        "привет, как дела"
    )
    shot = {"message_id": 80, "photo": [{"file_id": "x"}]}
    assert ar._language_words(
        shot, "[a photo showing: a sign; text in it: fermé le lundi]"
    ) == "fermé le lundi"
    assert ar._language_words({"caption": "regarde"}, "regarde\n[a photo showing: x]") == "regarde"


async def test_a_captionless_photo_reply_follows_the_conversation_language(monkeypatch):
    captured = {}

    def fake_llm(messages, **_):
        captured["user"] = messages[1]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    await ar._generate("tyler", "", [("Them", "смотри")], target="[a photo showing: a car]",
                       lang_words="")
    assert "same language as this: «смотри»" in captured["user"]
    await ar._generate("tyler", "", [], target="[a photo showing: a car]", lang_words="")
    assert "same language as this" not in captured["user"], "no words at all: no English pin"
