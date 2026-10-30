"""Tests for `navig.telegram.kill_card` — the process killer's four states."""

from __future__ import annotations

from typing import Any

import pytest

from navig.telegram import kill_card

CANDIDATES = [
    {"pid": 4242, "name": "chrome.exe"},
    {"pid": 4243, "name": "chrome.exe"},
    {"pid": 4244, "name": "chrome.exe"},
    {"pid": 4245, "name": "chrome.exe"},
    {"pid": 4246, "name": "chrome.exe"},
    {"pid": 4247, "name": "chrome.exe"},
    {"pid": 4248, "name": "chrome.exe"},
]


class _Channel:
    """A channel with a classic edit seam and no rich messages."""

    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    async def edit_message(self, chat_id, message_id, text, parse_mode="HTML", keyboard=None):
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "keyboard": keyboard}
        )
        return {"message_id": message_id}

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"text": text, **kwargs})
        return {"message_id": 11}


def test_the_usage_card_is_copyable_and_rehearses_two_groups_per_row() -> None:
    card = kill_card.usage_card(groups=["node", "python", "chrome-tabs"])
    html = card.rich_html()
    assert "<h2>🛑 Kill processes</h2>" in html
    assert '<code class="language-text">/kill &lt;group|name&gt; [--force] [--dry-run]</code>' in html
    assert "<code>chrome-tabs</code>" in html
    assert 'copy_text' in html
    rows = card.keyboard()
    assert [b["text"] for b in rows[0]] == ["🔍 chrome-tabs", "🔍 node"]
    assert rows[0][0]["callback_data"] == "nav:cmd:/kill chrome-tabs --dry-run"


def test_a_missing_match_is_a_card_not_a_sentence_in_a_send() -> None:
    card = kill_card.no_match_card("ghost")
    html = card.rich_html()
    assert "No matching processes." in html
    assert "/kill &lt;group|name&gt;" in html
    assert card.keyboard()[0][0]["copy_text"]["text"].startswith("/kill")


def test_a_dry_run_lists_the_candidates_and_still_offers_the_real_thing() -> None:
    card = kill_card.dry_run_card("chrome-tabs", CANDIDATES, total=9)
    html = card.rich_html()
    assert "<h2>🔍 Dry run</h2>" in html
    assert "9 candidate(s) would be killed" in html
    assert "Nothing was killed" in html
    assert "<code>4242</code>" in html
    assert "… +2 more" in html
    kill_button = card.keyboard()[0][0]
    assert kill_button["text"] == "🛑 Kill 9 now"
    assert kill_button["callback_data"] == "kill_confirm:chrome-tabs:soft"
    assert kill_button["style"] == "danger"


def test_a_forced_dry_run_does_not_offer_a_second_soft_row() -> None:
    card = kill_card.dry_run_card("chrome-tabs", CANDIDATES, total=7, force=True)
    callbacks = [b["callback_data"] for row in card.keyboard() for b in row]
    assert callbacks == ["kill_confirm:chrome-tabs:force", "kill_cancel"]


def test_the_confirmation_card_states_the_mode_and_keeps_the_list_short() -> None:
    card = kill_card.confirm_card("chrome-tabs", CANDIDATES, total=7)
    html = card.rich_html()
    # The confirm is the only card that asks a question big enough for h1.
    assert "<h1>🛑 Confirm kill</h1>" in html
    assert "7 process(es) match" in html
    assert html.count("<code>42") == 5
    assert "… +2 more" in html
    assert "graceful terminate" in html
    assert "<aside>" not in html  # no warning for a group that cannot take the daemon
    assert card.footer == "Nothing runs until you press Kill."


def test_a_self_risky_group_gets_the_warning_callout() -> None:
    card = kill_card.confirm_card("python", CANDIDATES[:2], total=2, self_risky=True)
    html = card.rich_html()
    assert "<aside>" in html
    assert "NAVIG daemon" in html
    assert card.footer and "NAVIG daemon" in str(card.footer)


def test_the_result_card_reports_the_three_buckets_and_the_reasons() -> None:
    card = kill_card.result_card(
        "chrome-tabs",
        killed=[{"pid": 1}, {"pid": 2}],
        skipped=[{"pid": 3}],
        failed=[{"pid": 4, "reason": "access denied"}],
    )
    html = card.rich_html()
    assert "<h2>🛑 Kill complete</h2>" in html
    assert "<b>2</b>" in html and "<b>1</b>" in html
    assert "access denied" in html
    assert card.keyboard()[0][0]["text"] == "🔍 Rehearse again"


def test_a_clean_kill_is_a_success_glyph_and_no_reason_table() -> None:
    card = kill_card.result_card("node", killed=[{"pid": 9}])
    html = card.rich_html()
    assert "<h2>✅ Kill complete</h2>" in html
    assert "access denied" not in html
    assert card.keyboard()[0][1]["callback_data"] == "kill_cancel"


def test_a_cancelled_card_has_no_buttons_at_all() -> None:
    card = kill_card.cancelled_card()
    assert card.keyboard() is None
    assert "Nothing was touched" in card.rich_html()


@pytest.mark.asyncio
async def test_cancelling_empties_the_keyboard_instead_of_omitting_it() -> None:
    channel = _Channel()
    assert await kill_card.cancel(channel, 5, 77) is True
    assert channel.edits[-1]["message_id"] == 77
    # None would leave the Kill button armed; an empty list removes it.
    assert channel.edits[-1]["keyboard"] == []
    assert "Kill cancelled" in channel.edits[-1]["text"]


def test_the_classic_projection_carries_no_rich_only_tag() -> None:
    for card in (
        kill_card.usage_card(groups=["node"]),
        kill_card.no_match_card("ghost"),
        kill_card.dry_run_card("node", CANDIDATES[:2], total=2),
        kill_card.confirm_card("python", CANDIDATES[:2], total=2, self_risky=True),
        kill_card.result_card("node", killed=[{"pid": 1}]),
    ):
        classic = card.classic_html()
        for tag in ("<h2", "<table", "<tg-button", "<footer", "<aside", "<pre><code"):
            assert tag not in classic, (tag, classic[:200])

# -- the handler and its confirm callback --------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []

    async def send_card(self, channel, chat_id, card, **kwargs):
        self.sends.append({"chat_id": chat_id, "card": card})
        return {"message_id": 100 + len(self.sends)}

    async def edit_card(self, channel, chat_id, message_id, card, **kwargs):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "card": card})
        return True


def _commands(monkeypatch, recorder: _Recorder):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.telegram import card_transport

    monkeypatch.setattr(card_transport, "send_card", recorder.send_card)
    monkeypatch.setattr(card_transport, "edit_card", recorder.edit_card)
    return TelegramCommandsMixin


@pytest.mark.asyncio
async def test_the_handler_sends_one_usage_card_when_no_group_is_named(monkeypatch) -> None:
    recorder = _Recorder()
    commands = _commands(monkeypatch, recorder)

    await commands._handle_kill_cmd(object(), 1, 1, "/kill")

    assert len(recorder.sends) == 1
    assert "Kill processes" in recorder.sends[0]["card"].rich_html()


@pytest.mark.asyncio
async def test_a_dry_run_rewrites_the_pressed_message_and_posts_nothing(monkeypatch) -> None:
    recorder = _Recorder()
    commands = _commands(monkeypatch, recorder)
    from navig.commands import processes as proc

    monkeypatch.setattr(proc, "find_processes_by_group", lambda _t: CANDIDATES)

    await commands._handle_kill_cmd(
        object(), 1, 1, "/kill chrome-tabs --dry-run", message_id=55
    )

    assert recorder.sends == []
    assert recorder.edits[0]["message_id"] == 55
    assert "Dry run" in recorder.edits[0]["card"].rich_html()


@pytest.mark.asyncio
async def test_the_handler_only_enumerates_and_never_kills(monkeypatch) -> None:
    recorder = _Recorder()
    commands = _commands(monkeypatch, recorder)
    from navig.commands import processes as proc

    monkeypatch.setattr(proc, "find_processes_by_group", lambda _t: CANDIDATES)
    monkeypatch.setattr(
        proc, "kill_group", lambda *a, **k: pytest.fail("the handler must not kill")
    )

    await commands._handle_kill_cmd(object(), 1, 1, "/kill chrome-tabs")

    assert len(recorder.sends) == 1
    card = recorder.sends[0]["card"]
    assert card.keyboard()[0][0]["callback_data"] == "kill_confirm:chrome-tabs:soft"


@pytest.mark.asyncio
async def test_the_confirm_callback_rewrites_the_card_it_came_from(monkeypatch) -> None:
    recorder = _Recorder()
    commands = _commands(monkeypatch, recorder)
    from navig.commands import processes as proc

    monkeypatch.setattr(
        proc,
        "kill_group",
        lambda target, force=False, exclude_self=True, dry_run=False: {
            "killed": [{"pid": 42}],
            "skipped": [],
            "failed": [],
        },
    )

    await commands._handle_kill_confirm_callback(
        object(), 1, 1, "kill_confirm:node:soft", message_id=88
    )

    assert recorder.sends == []
    edited = recorder.edits[0]
    assert edited["message_id"] == 88
    callbacks = [b["callback_data"] for row in edited["card"].keyboard() for b in row]
    assert "kill_confirm:node:soft" not in callbacks
    assert "Kill complete" in edited["card"].rich_html()
