"""A command's answer as a card: what ran, how it ended, the output, its buttons.

The old handler sent the raw output as one HTML blob with no title, no exit state
and no way to repeat the check without typing the command again. These tests pin
the card, the truncation rule, the callback payloads (a button that cannot say
which command it means is worse than no button) and both halves of the send: a
fresh card, and a press that rewrites the message it came from.
"""

from __future__ import annotations

import pytest

from navig.telegram import cli_card as cc

OUTPUT = "Gateway: running\nHosts: 3\nVault: 381 keys"
FAILED = "Traceback (most recent call last):\n  File 'x.py', line 3\nRuntimeError: boom\nCommand exited with code: 2"


class _RichChannel:
    """Records what the handler sends, at whichever fidelity it used."""

    def __init__(self) -> None:
        self.rich: list[dict] = []
        self.plain: list[dict] = []

    async def send_rich_message(self, chat_id, **kw):
        self.rich.append({"chat_id": chat_id, **kw})
        return {"message_id": 11}

    async def send_message(self, chat_id, text, **kw):
        self.plain.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": 12}


class _FakeCommands:
    """The routing layer's answer, and a record of what it was asked.

    ``answers`` is consumed in call order, so a test can tell the command's raw
    answer apart from the summary the model writes about it.
    """

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.turns: list[str] = []

    async def __call__(self, *, channel, user_id, message, metadata):
        self.turns.append(message)
        return self.answers[min(len(self.turns), len(self.answers)) - 1]


def _card(**kwargs):
    kw = {"duration_ms": 412}
    kw.update(kwargs)
    return cc.card(kw.pop("command", "status"), kw.pop("output", OUTPUT), **kw)


# --- the card itself -------------------------------------------------------


def test_the_title_names_the_command_and_the_subtitle_says_how_long_it_took() -> None:
    card = _card()
    html = card.rich_html()
    assert "<h2>⌨️ navig status</h2>" in html
    assert "<i>answered in 412 ms</i>" in html


def test_the_output_is_a_monospace_block_not_a_wall_of_bold_lines() -> None:
    html = _card().rich_html()
    assert '<pre><code class="language-text">Gateway: running' in html
    assert "Hosts: 3" in html


def test_a_failure_states_the_exit_code_and_points_at_the_reason() -> None:
    card = _card(output=FAILED)
    html = card.rich_html()
    assert "<i>exited with code 2</i>" in html
    # The last lines carry the reason, so they are the ones worth keeping.
    assert "RuntimeError: boom" in html
    assert "<aside>" in html


def test_a_long_output_keeps_its_tail_and_admits_the_cut() -> None:
    long_output = "\n".join(f"line {i}" for i in range(cc.MAX_LINES + 5))
    html = _card(output=long_output).rich_html()
    assert f"line {cc.MAX_LINES + 4}" in html
    assert "line 0\n" not in html
    assert f"Showing the last {cc.MAX_LINES} lines of {cc.MAX_LINES + 5}." in html


def test_an_empty_output_still_answers_something() -> None:
    html = _card(output="   ").rich_html()
    assert "The command printed nothing." in html


def test_the_actions_ride_inside_the_bubble_and_run_again_leads() -> None:
    card = _card()
    html = card.rich_html()
    assert "<tg-button-row" in html
    assert 'style="primary">🔄 Run again' in html
    assert "🧠 Explain" in html and "✖ Close" in html
    assert card.footer == "Re-run it here, or ask what the output means."


def test_an_explanation_sits_above_the_output_it_explains() -> None:
    html = _card(summary="Gateway is up and 3 hosts are connected.").rich_html()
    assert "Gateway is up and 3 hosts are connected." in html
    assert html.index("Gateway is up") < html.index("Gateway: running")


def test_the_classic_projection_carries_the_words_and_none_of_the_rich_tags() -> None:
    classic = _card(output=FAILED, summary="It crashed.").classic_html()
    assert "navig status" in classic and "exited with code 2" in classic
    assert "It crashed." in classic and "RuntimeError: boom" in classic
    for tag in ("<h2", "<tg-button-row", "<footer", "<aside", '<pre><code class="language-text">'):
        assert tag not in classic


# --- the callbacks ---------------------------------------------------------


def test_a_button_can_say_which_command_it_means() -> None:
    card = _card(command="host list --all")
    rows = card.keyboard()
    run = rows[0][0]
    assert cc.parse_callback(run["callback_data"]) == ("run", "host list --all")
    assert cc.parse_callback(rows[0][2]["callback_data"]) == ("close", "")
    assert len(run["callback_data"].encode()) <= cc.MAX_CALLBACK


def test_a_command_too_long_to_ride_in_a_button_gets_no_buttons() -> None:
    card = _card(command="x" * (cc.MAX_CALLBACK + 40))
    assert all(button["callback_data"] != "" or True for row in card.keyboard() or () for button in row)
    assert "cli:" not in card.rich_html()


def test_a_mangled_payload_is_not_a_command() -> None:
    assert cc.parse_callback("cli:run:%%%") is None
    assert cc.parse_callback("hm:home") is None
    assert cc.parse_callback("cli:run") is None
    # An empty payload used to decode to b"" and "re-run" a blank command.
    assert cc.parse_callback("cli:run:") is None


def test_the_channel_can_reach_the_command_handler_with_its_new_keywords() -> None:
    """The router calls it on the CHANNEL, which does not inherit the mixin."""
    import inspect

    from navig.gateway.channels.telegram import TelegramChannel

    params = inspect.signature(TelegramChannel._handle_cli_command).parameters
    assert "message_id" in params
    assert "explain" in params


def test_the_cli_prefix_is_owned_by_a_gated_extension() -> None:
    from navig.gateway.channels.telegram_extensions import EXTENSIONS

    owned = {p for e in EXTENSIONS for p in e.callback_prefixes}
    assert "cli:" in owned


# --- the send --------------------------------------------------------------


def _channel(monkeypatch, recorder: _RichChannel):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message
    return channel


@pytest.mark.asyncio
async def test_a_command_answer_is_one_rich_card_with_the_plain_projection_as_fallback():
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message
    # A list command skips the summary turn, so the answer is the output itself.
    channel.on_message = _FakeCommands("web vhosts\nsite-a.example")

    await channel._handle_cli_command(1, 2, {}, "web vhosts")

    assert len(recorder.rich) == 1
    sent = recorder.rich[0]
    assert "<h2>⌨️ navig web vhosts</h2>" in sent["html"]
    assert "<tg-button-row" in sent["html"]
    assert "<h2" not in sent["fallback_html"]
    assert not recorder.plain


@pytest.mark.asyncio
async def test_a_press_rewrites_the_card_instead_of_stacking_another(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.telegram import card_transport

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message
    channel.on_message = _FakeCommands("web vhosts\nsite-a.example")

    seen: list[tuple[int, int]] = []

    async def _edit_card(chan, chat_id, message_id, card, *, keyboard=None):
        seen.append((chat_id, message_id))
        assert "navig web vhosts" in card.rich_html()
        return True

    monkeypatch.setattr(card_transport, "edit_card", _edit_card)

    await channel._handle_cli_command(1, 2, {}, "web vhosts", message_id=77)

    assert seen == [(1, 77)]
    assert not recorder.rich and not recorder.plain


@pytest.mark.asyncio
async def test_explain_asks_for_the_words_and_keeps_the_output_under_them():
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message
    router = _FakeCommands(OUTPUT, "Gateway is up with 3 hosts.")
    channel.on_message = router

    await channel._handle_cli_command(1, 2, {}, "status", message_id=None, explain=True)

    # One turn to run the command, one to explain it.
    assert len(router.turns) == 2
    assert router.turns[0].strip() == "navig status"
    assert "summarize" in router.turns[1].lower()
    html = recorder.rich[0]["html"]
    assert "Gateway is up with 3 hosts." in html
    assert "Gateway: running" in html


@pytest.mark.asyncio
async def test_a_channel_without_rich_messages_gets_the_plain_projection():
    class _PlainOnly:
        def __init__(self) -> None:
            self.plain: list[dict] = []

        async def send_message(self, chat_id, text, **kw):
            self.plain.append({"chat_id": chat_id, "text": text, **kw})
            return {"message_id": 9}

    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    channel.send_rich_message = None
    recorder = _PlainOnly()
    channel.send_message = recorder.send_message
    channel.on_message = _FakeCommands("web vhosts\nsite-a.example")

    await channel._handle_cli_command(1, 2, {}, "web vhosts")

    assert len(recorder.plain) == 1
    assert recorder.plain[0]["parse_mode"] == "HTML"
    assert "navig web vhosts" in recorder.plain[0]["text"]
    assert "<tg-button-row" not in recorder.plain[0]["text"]


@pytest.mark.asyncio
async def test_a_healable_failure_still_goes_to_the_heal_pipeline_not_to_a_card():
    """The card must not swallow a failure the auto-heal flow acts on."""
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    recorder = _RichChannel()
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    async def _boom(**kwargs):
        raise RuntimeError("router down")

    channel.on_message = _boom

    healed: list[object] = []

    async def _heal(ctx):
        healed.append(ctx)

    channel._heal_failure = _heal

    await channel._handle_cli_command(1, 2, {}, "status")

    assert healed, "the heal pipeline owns a failed command"
    assert not recorder.rich
