"""An answered weigh-in must always produce a visible acknowledgement.

Reported from the operator's own chat: the bot asked "⚖️ Вес сегодня утром?",
they replied `125`, and nothing came back — *"the weight doesn't count, even if it
asks me"*. The weight DID land (`metrics.csv` holds `2026-09-08,125`); the receipt
did not, and from the chat those are the same thing.

`settle_prompt` rewrites the prompt in place instead of posting a second message,
and the caller only sends its `✅ {confirmation}` fallback when that edit did NOT
land. But it returned True whenever `_api_call` RETURNED — and `_api_call` reports
a rejected call by returning **None**, not by raising. So "message is not
modified", a message too old to edit, or one deleted from the chat all took the
success path and the fallback never fired.

⚠ This is the class already written down in this repo for `send_video`: *"returns
None on a REJECTED send without raising, so the except below never fires.
Treating that as success discarded the only copy of a video the user never
received."* Same shape, different method — which is why these tests assert the
**contract of the return value**, not just the happy path.

⚖ The rest of the tree was checked for the same shape: 93 functions call
`_api_call`; 45 bind and consult the result, 45 discard it with no success claim
(legitimately best-effort), and the 3 that looked like this one were all false
positives on inspection (`return True` there means "let the button press through",
or the real send is delegated to a helper that does check). One real instance, so
this is a fix rather than a new guard.
"""

from __future__ import annotations

from typing import Any

import pytest

from navig.telegram import body_actions as ba


class _Channel:
    """A channel whose `_api_call` behaves the way the real one does.

    The real `_api_call` returns `result["result"]` when Telegram says ok, and
    falls through to **None** otherwise. It raises only on transport errors.
    """

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[tuple[str, dict]] = []
        self.sent: list[str] = []

    async def _api_call(self, method: str, data: dict | None = None) -> Any:
        self.calls.append((method, data or {}))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def send_message(self, chat_id: int, text: str, **_: Any) -> dict:
        self.sent.append(text)
        return {"message_id": 999}


@pytest.mark.asyncio
async def test_a_rejected_edit_is_not_reported_as_settled() -> None:
    """The bug, at the layer it lives in.

    `None` is how `_api_call` says "Telegram refused this" — the exact shape that
    used to return True and swallow the operator's confirmation.
    """
    channel = _Channel(result=None)

    settled = await ba.settle_prompt(channel, 42, 7, "⚖️ 125 kg recorded")

    assert settled is False, (
        "a rejected editMessageText was reported as settled, so the caller "
        "skipped the confirmation and the operator saw silence"
    )
    assert channel.calls and channel.calls[0][0] == "editMessageText"


@pytest.mark.asyncio
async def test_a_successful_edit_is_reported_as_settled() -> None:
    """The fix must not make every edit look failed — that would post a second
    message on top of a correctly-rewritten prompt, which is the noise the
    in-place edit exists to avoid."""
    channel = _Channel(result={"message_id": 7, "text": "⚖️ 125 kg recorded"})

    assert await ba.settle_prompt(channel, 42, 7, "⚖️ 125 kg recorded") is True


@pytest.mark.asyncio
async def test_a_transport_error_is_not_reported_as_settled() -> None:
    """The path that already worked, kept working."""
    channel = _Channel(result=RuntimeError("connection reset"))

    assert await ba.settle_prompt(channel, 42, 7, "anything") is False


@pytest.mark.asyncio
async def test_the_edit_clears_the_prompt_buttons() -> None:
    """A settled prompt must not leave live buttons behind — omitting
    `reply_markup` leaves the old keyboard on a message that now says the day is
    recorded."""
    channel = _Channel(result={"message_id": 7})

    await ba.settle_prompt(channel, 42, 7, "⚖️ recorded")

    _method, payload = channel.calls[0]
    assert payload.get("reply_markup") == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_the_operator_is_told_when_the_prompt_cannot_be_rewritten(
    monkeypatch, tmp_path
) -> None:
    """The end the whole change is for: answer the weigh-in, see SOMETHING.

    Drives the real caller with an edit that Telegram refuses, and asserts a
    confirmation message is sent instead of the answer vanishing.
    """
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    channel = _Channel(result=None)  # every edit refused

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    handled = await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    assert handled is True, "the answer must be consumed, not fall through to chat"
    assert channel.sent, (
        "the prompt could not be rewritten AND no confirmation was sent — the "
        "operator answered the weigh-in and got silence"
    )
    assert "125" in channel.sent[0]


@pytest.mark.asyncio
async def test_a_successful_edit_still_sends_a_receipt(monkeypatch, tmp_path) -> None:
    """The half #1318 did not cover — and the half the operator actually hit.

    #1318 rescued the FAILING edit. But an edit is invisible in every client that
    matters: it raises no notification and does not move the message to the
    bottom of the chat. So when the edit SUCCEEDS and anything arrives
    afterwards — on the reported day, a cron reminder five minutes later — the
    answer still reads as discarded.

    Settling the prompt is CLEANUP (it retires a stale force_reply question).
    The receipt is a MESSAGE. Conflating them is what produced the silence.
    """
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    channel = _Channel(result={"message_id": 7})  # every edit ACCEPTED

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    handled = await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    assert handled is True
    assert channel.sent, (
        "the edit landed so no message was sent — but an edit does not notify and "
        "does not move to the bottom of the chat, so the operator answered the "
        "weigh-in and saw nothing new"
    )
    assert "125" in channel.sent[0]


@pytest.mark.asyncio
async def test_the_stale_question_is_still_retired(monkeypatch, tmp_path) -> None:
    """Always sending a receipt must not cost us the cleanup.

    The prompt carries force_reply, and Telegram clients re-arm that reply box
    after a restart, quoting the original text — so an answered check-in would
    look like it is being asked again. It must be retired, by whichever call
    works: deleted (which disarms the box) or, failing that, rewritten.
    """
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    channel = _Channel(result={"message_id": 7})

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    assert any(m in ("deleteMessage", "editMessageText") for m, _ in channel.calls), (
        "the stale force_reply question was left standing"
    )


@pytest.mark.asyncio
async def test_exactly_one_acknowledgement_per_answer(monkeypatch, tmp_path) -> None:
    """One answer, one new message — the receipt must not become a double-post."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    channel = _Channel(result={"message_id": 7})

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    assert len(channel.sent) == 1, f"sent {len(channel.sent)} messages for one answer"


# ── Disarming the reply box, not just rewriting its text ─────────────────────
#
# Reported 2026-09-27, in the operator's words: the bot "asks me a weight a
# couple of times a day". It does not — `cron_runs.jsonl` records `job_31
# habit:weigh` firing once a day at 08:10 for eighteen consecutive days, and each
# run's output is a single "✓ Weigh-in sent". What repeats is the CLIENT's
# force_reply box: it stays pointed at the prompt's message id and re-arms itself
# on every app restart, rendering its own cached copy of the original question.
#
# Rewriting the message (all this used to do) changes the text but not the
# target: not one `editMessageText` was rejected in the log that day, and at
# 14:52 the compose box still read "⚖️ Вес сегодня утром? Прошлый раз: 125 кг,
# 26.09" — a question answered at 14:10, quoting a value already superseded.
# There is no API to clear a force_reply (`editMessageReplyMarkup` takes an
# inline keyboard only), so the message has to go.


@pytest.mark.asyncio
async def test_an_answered_prompt_is_deleted_not_merely_rewritten(monkeypatch, tmp_path) -> None:
    """The fix. Deleting is the only thing that disarms the reply box."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    channel = _Channel(result={"message_id": 7})

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    methods = [m for m, _ in channel.calls]
    assert "deleteMessage" in methods, (
        "the prompt was not deleted, so the client's force_reply box stays armed "
        "and re-quotes the morning's question for the rest of the day"
    )
    assert "editMessageText" not in methods, (
        "a deleted prompt must not also be edited — the edit would fail, and its "
        "warning would report a problem that does not exist"
    )


@pytest.mark.asyncio
async def test_a_prompt_too_old_to_delete_falls_back_to_the_rewrite(monkeypatch, tmp_path) -> None:
    """Telegram refuses a bot delete past 48 h. Answering a two-day-old prompt is
    exactly when the operator most needs the question to stop reading as open, so
    the old mitigation stays as the fallback rather than being replaced."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    class _DeleteRefused(_Channel):
        async def _api_call(self, method: str, data: dict | None = None):
            self.calls.append((method, data or {}))
            if method == "deleteMessage":
                return None  # how `_api_call` reports a refusal
            return {"message_id": 7}

    channel = _DeleteRefused(result=None)

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    methods = [m for m, _ in channel.calls]
    assert methods.index("deleteMessage") < methods.index("editMessageText"), (
        "delete must be TRIED first — the rewrite is only for prompts too old to delete"
    )
    assert channel.sent, "and the receipt is still sent"


@pytest.mark.asyncio
async def test_deleting_the_prompt_does_not_lose_the_seven_day_average(
    monkeypatch, tmp_path
) -> None:
    """The rewritten prompt was where the 7-day average was shown. Deleting it
    would drop that number silently, so the receipt — the one message that
    survives — has to carry it."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.spaces import body_metrics as bm

    channel = _Channel(result={"message_id": 7})

    monkeypatch.setattr(bm, "pending_prompt", lambda _chat: ("weigh", "2026-09-08", 7))
    monkeypatch.setattr(bm, "resolve_target", lambda _chat: tmp_path / "metrics.csv")
    monkeypatch.setattr(bm, "latest", lambda _path: ("2026-09-08", 125.0))
    monkeypatch.setattr(bm, "moving_average", lambda _p, days=7, ending=None: 124.2)
    monkeypatch.setattr(ba, "consume_reply", lambda _chat, _text: "125 kg recorded")

    await TelegramChannel._handle_pending_body_input(
        channel, chat_id=42, text="125", reply_to_message_id=7
    )

    assert len(channel.sent) == 1, "still exactly one message per answer"
    assert "124.2" in channel.sent[0], (
        f"the 7-day average vanished with the deleted prompt: {channel.sent[0]!r}"
    )
    assert "125" in channel.sent[0], "and the reading itself is still acknowledged"


@pytest.mark.asyncio
async def test_a_refused_delete_is_not_reported_as_dismissed() -> None:
    """`_api_call` reports a rejected call by returning None rather than raising —
    the same trap that made `settle_prompt` swallow the receipt. Reporting a
    refused delete as done would skip the fallback and leave the box armed."""
    channel = _Channel(result=None)

    assert await ba.dismiss_prompt(channel, 42, 7) is False
    assert channel.calls and channel.calls[0][0] == "deleteMessage"


@pytest.mark.asyncio
async def test_a_transport_error_is_not_reported_as_dismissed() -> None:
    channel = _Channel(result=RuntimeError("connection reset"))

    assert await ba.dismiss_prompt(channel, 42, 7) is False


@pytest.mark.asyncio
async def test_a_successful_delete_is_reported_as_dismissed() -> None:
    """Telegram answers `deleteMessage` with `True`, which `_api_call` passes
    through — that must not be mistaken for a refusal."""
    channel = _Channel(result=True)

    assert await ba.dismiss_prompt(channel, 42, 7) is True
