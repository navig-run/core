"""A role reply is fast, visible while it works, and never destroys anything.

Asked 2026-09-29: "it takes a while when the answer is coming — investigate why;
when I type tyler, don't delete the message but edit it with 'I'm thinking' or a
loading state". Measured on the live install, per reply:

* the model: 7–16 s (local 7B; the first call after idle also loads it);
* a deliberate "human typing" delay of ~16 s for a 180-character reply, up to ~34 s.

The delay makes sense when the bot answers ON ITS OWN (pro mode) and none when the
owner triggered it, so the owner-triggered paths no longer wait. And the trigger is
no longer deleted: it becomes 👀 / ✍️ while the answer is made, the other person
sees "typing…", and the finished answer is edited into that same message — which is
already a reply to the right one. If Telegram refuses the edit, the old path runs.

Rewriting your own message now edits it IN PLACE; and a rewrite on your own photo
edits only the caption — the old send-then-delete would have deleted the photo.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import navig.telegram.autoreply as ar


class _Chan:
    """Records every call; `refuse` names the methods Telegram turns down."""

    def __init__(self, refuse: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.refuse = set(refuse)
        self.send_message = AsyncMock()

    async def _api_call(self, method, data=None):
        self.calls.append((method, data or {}))
        if method in self.refuse:
            return None
        return {"message_id": 999} if method == "sendMessage" else True

    def of(self, method):
        return [d for m, d in self.calls if m == method]

    def deleted(self):
        return [i for d in self.of("deleteBusinessMessages") for i in d["message_ids"]]


def _their(text="Сколько стоит доставка?", **extra):
    return {"message_id": 80, "text": text, "from": {"id": 555}, **extra}


def _trigger(target, text="tyler"):
    return {
        "chat": {"id": 555},
        "message_id": 90,
        "business_connection_id": "bc1",
        "from": {"id": 777},
        "text": text,
        "reply_to_message": target,
    }


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(ar, "_recent_context", lambda cid, oid, n=12: [])
    monkeypatch.setattr(ar, "_chat_action", AsyncMock())


async def _run(ch, msg):
    assert await ar.maybe_role_reply(ch, msg, is_owner=True, owner_id=777)
    await asyncio.gather(*list(ar._bg_tasks))


# ── the trigger becomes the answer ───────────────────────────────────────────


async def test_the_trigger_becomes_a_loading_state_then_the_answer(monkeypatch):
    monkeypatch.setattr(
        ar, "_generate", AsyncMock(return_value="Цену не знаю. Спроси отправителя.")
    )
    ch = _Chan()
    await _run(ch, _trigger(_their()))

    edits = ch.of("editMessageText")
    assert [e["text"] for e in edits] == ["✍️", "Цену не знаю. Спроси отправителя."]
    assert all(e["message_id"] == 90 and e["business_connection_id"] == "bc1" for e in edits)
    assert ch.deleted() == [], "nothing is deleted when the edit works"
    assert ch.of("sendMessage") == [], "one message: the trigger, answered in place"


async def test_a_photo_shows_reading_first(monkeypatch):
    monkeypatch.setattr(ar, "_message_as_text", AsyncMock(return_value="[a photo showing: a car]"))
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Машина тебя не спасёт."))
    ch = _Chan()
    photo = {"message_id": 80, "from": {"id": 555}, "photo": [{"file_id": "P", "file_size": 10}]}
    await _run(ch, _trigger(photo))
    assert [e["text"] for e in ch.of("editMessageText")] == ["👀", "✍️", "Машина тебя не спасёт."]


async def test_the_other_person_sees_typing_while_it_is_written(monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(0)
        return "ok"

    monkeypatch.setattr(ar, "_generate", slow)
    ch = _Chan()
    await _run(ch, _trigger(_their()))
    assert ar._chat_action.await_count >= 1


async def test_an_owner_triggered_reply_has_no_artificial_delay(monkeypatch):
    """About half the wait was fake typing — for something the owner asked for."""
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="ok"))
    # A spy, checked after — NOT a raising stub: the task swallows exceptions by
    # design, so a raise inside it would pass silently (it did, until a mutation
    # that put the delay back went green).
    human = AsyncMock(return_value=True)
    monkeypatch.setattr(ar, "_human_send", human)
    await _run(_Chan(), _trigger(_their()))
    await _run(_Chan(refuse=("editMessageText",)), _trigger(_their()))
    human.assert_not_awaited()


async def test_a_refused_edit_falls_back_to_delete_and_reply(monkeypatch):
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="ok"))
    ch = _Chan(refuse=("editMessageText",))
    await _run(ch, _trigger(_their()))
    assert ch.deleted() == [90], "the trigger is removed the old way"
    sent = ch.of("sendMessage")
    assert len(sent) == 1 and sent[0]["reply_parameters"]["message_id"] == 80


async def test_a_failure_never_leaves_a_loading_state_behind(monkeypatch):
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value=""))
    ch = _Chan()
    await _run(ch, _trigger(_their()))
    assert ch.deleted() == [90], "the ✍️ must not stay in their chat"
    assert "Couldn't write" in ch.send_message.await_args.args[1]


async def test_pro_mode_keeps_its_human_pacing(monkeypatch):
    """Only the owner-triggered paths lost the delay; auto-replies still look human."""
    human = AsyncMock(return_value=True)
    monkeypatch.setattr(ar, "_human_send", human)
    monkeypatch.setattr(ar, "get_active", lambda cid: {"role": "tyler", "lang": ""})
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="ok"))
    msg = {"chat": {"id": 555}, "business_connection_id": "bc1", **_their()}
    assert await ar.maybe_autoreply(_Chan(), msg, is_owner=False, owner_id=777)
    await asyncio.gather(*list(ar._bg_tasks))
    human.assert_awaited_once()


# ── your own message is rewritten in place ────────────────────────────────────


def _mine(**fields):
    return {"message_id": 80, "from": {"id": 777}, **fields}


async def test_your_text_is_rewritten_in_place(monkeypatch):
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Завтра. Шесть. Не опаздывай."))
    ch = _Chan()
    await _run(ch, _trigger(_mine(text="завтра в 6, не опаздывай")))

    final = [e for e in ch.of("editMessageText") if e["message_id"] == 80]
    assert final and final[0]["text"] == "Завтра. Шесть. Не опаздывай.", "the original, edited"
    assert ch.deleted() == [90], "only the loading placeholder goes; the original stays in place"
    assert ch.of("sendMessage") == []


async def test_a_refused_in_place_edit_falls_back_to_send_then_delete(monkeypatch):
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Да."))
    ch = _Chan(refuse=("editMessageText",))
    await _run(ch, _trigger(_mine(text="да", reply_to_message={"message_id": 70})))
    sent = ch.of("sendMessage")
    assert sent and sent[0]["reply_parameters"]["message_id"] == 70, "keeps its thread"
    assert 80 in ch.deleted(), "the original goes only AFTER the new one was sent"


async def test_when_everything_is_refused_your_words_stay(monkeypatch):
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Да."))
    ch = _Chan(refuse=("editMessageText", "sendMessage"))
    await _run(ch, _trigger(_mine(text="да")))
    assert 80 not in ch.deleted()
    assert "left as it was" in ch.send_message.await_args.args[1]


async def test_your_captioned_photo_gets_a_new_caption_and_keeps_the_photo(monkeypatch):
    monkeypatch.setattr(ar, "_message_as_text", AsyncMock(return_value="look at this\n[a photo]"))
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="Смотри. Или не смотри."))
    ch = _Chan()
    await _run(
        ch, _trigger(_mine(caption="look at this", photo=[{"file_id": "P", "file_size": 1}]))
    )
    caps = ch.of("editMessageCaption")
    assert caps and caps[0]["message_id"] == 80 and caps[0]["caption"] == "Смотри. Или не смотри."
    assert 80 not in ch.deleted(), "the photo is never deleted"


async def test_your_photo_without_a_caption_is_never_touched(monkeypatch):
    """The old send-then-delete would have REPLACED the photo with text."""
    gen = AsyncMock(return_value="x")
    monkeypatch.setattr(ar, "_generate", gen)
    ch = _Chan()
    assert await ar.maybe_role_reply(
        ch, _trigger(_mine(photo=[{"file_id": "P", "file_size": 1}])), is_owner=True, owner_id=777
    )
    gen.assert_not_awaited()
    assert 80 not in ch.deleted() and ch.of("editMessageCaption") == []
    assert "Nothing for" in ch.send_message.await_args.args[1]


# ── the prompt ────────────────────────────────────────────────────────────────


def test_tyler_gives_the_brutal_truth_and_then_the_way_out():
    text = ar.resolve_role("tyler")
    assert "the brutal truth first, then what to actually do" in text


async def test_no_role_may_invent_a_price_or_a_promise(monkeypatch):
    """It goes out AS the owner; asked for a solution, a persona invents a price."""
    captured = {}

    def fake_llm(messages, **_):
        captured["system"] = messages[0]["content"]
        return "ok"

    import navig.llm.generate as gen_mod

    monkeypatch.setattr(gen_mod, "llm_generate", fake_llm)
    monkeypatch.setattr(ar, "_model_choice", lambda: {"mode": "big"})
    await ar._generate("sales", "", [], target="сколько стоит?")
    assert "Never invent facts" in captured["system"] and "prices" in captured["system"]


# ── a run can never hang with ✍️ up ──────────────────────────────────────────
#
# Reported 2026-09-30: `tyler` became ✍️ and then nothing — no outcome, no error —
# for three minutes, until a restart killed the task. The model call had a timeout;
# the RUN did not.


async def _stall(*_a, **_k):
    await asyncio.sleep(3600)


@pytest.mark.parametrize("target", [_their(), _mine(text="завтра в 6")], ids=["reply", "rewrite"])
async def test_a_stalled_run_gives_up_and_says_so(monkeypatch, target):
    monkeypatch.setattr(ar, "_ROLE_DEADLINE_S", 0.2)
    monkeypatch.setattr(ar, "_generate", _stall)
    ch = _Chan()
    await asyncio.wait_for(_run(ch, _trigger(target)), 5)
    assert 90 in ch.deleted(), "the ✍️ placeholder must not stay up"
    assert "took too long" in ch.send_message.await_args.args[1]
    assert 80 not in ch.deleted(), "the message it was about is untouched"


async def test_a_rewrite_that_changes_nothing_is_not_resent(monkeypatch):
    """A lone "*" comes back as "*". Telegram refuses an edit that changes nothing, which
    fell through to sending the same text again and deleting the original."""
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="*"))
    ch = _Chan()
    await _run(ch, _trigger(_mine(text="*")))
    assert ch.of("sendMessage") == []
    assert 80 not in ch.deleted()
    assert [d["message_id"] for d in ch.of("editMessageText")] == [90], (
        "only the trigger became ✍️; the original was not edited"
    )
    assert "nothing to change" in ch.send_message.await_args.args[1]


async def test_the_deadline_does_not_remove_an_answer_already_going_out(monkeypatch):
    """Past the deadline while the answer is being applied: removing the placeholder
    then would remove the answer itself."""
    monkeypatch.setattr(ar, "_ROLE_DEADLINE_S", 0.3)
    monkeypatch.setattr(ar, "_generate", AsyncMock(return_value="ответ"))
    real_edit = ar._edit_text

    async def slow_answer_edit(channel, chat_id, bcid, mid, text):
        if text == "ответ":
            await asyncio.sleep(3600)
        return await real_edit(channel, chat_id, bcid, mid, text)

    monkeypatch.setattr(ar, "_edit_text", slow_answer_edit)
    ch = _Chan()
    await asyncio.wait_for(_run(ch, _trigger(_their())), 5)
    assert 90 not in ch.deleted()
    ch.send_message.assert_not_awaited()
