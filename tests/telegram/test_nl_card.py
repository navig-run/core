"""The natural-language dispatcher's answers as cards."""

from __future__ import annotations

import pytest

from navig.telegram import nl_card

CANDIDATES = [
    {"command": "space", "usage": "/space <name>", "description": "Switch space"},
    {"command": "spaces", "usage": "/spaces", "description": "List spaces"},
    {"command": "span", "usage": "/span", "description": "Not a real command"},
    {"command": "spark", "usage": "/spark", "description": "Fourth, beyond the cap"},
]


def test_a_detected_intent_states_the_three_facts_and_both_answers() -> None:
    card = nl_card.intent_card(intent="switch", space="dev-space", delay=3)
    html = card.rich_html()
    assert "<h2>🧭 Space intent</h2>" in html
    assert "<td>Space</td><td><code>dev-space</code></td>" in html
    assert "in 3s" in html
    assert 'style="success"' in html and 'style="danger"' in html
    assert card.keyboard() == [
        [
            {"text": "✅ Yes now", "callback_data": "nl_yes", "style": "success"},
            {"text": "🛑 Cancel", "callback_data": "nl_cancel", "style": "danger"},
        ]
    ]


def test_an_ambiguous_match_lists_the_candidates_as_a_table() -> None:
    html = nl_card.ambiguous_card(candidates=CANDIDATES, query="spac").rich_html()
    assert "<h2>🧭 Which command?</h2>" in html
    assert "“spac”" in html
    assert "<td><code>/space &lt;name&gt;</code></td><td>Switch space</td>" in html
    # One pick per candidate, then a single cancel.
    assert html.count('data="nl_pick:') == 3
    assert html.count("nl_cancel") == 1


def test_a_candidate_without_a_command_is_not_offered() -> None:
    card = nl_card.ambiguous_card(candidates=[{"usage": "/ghost"}, *CANDIDATES])
    html = card.rich_html()
    assert "/ghost" not in html
    assert card.keyboard()[0][0]["callback_data"] == "nl_pick:space"


def test_the_candidates_cap_is_the_keyboards_cap() -> None:
    card = nl_card.ambiguous_card(candidates=CANDIDATES)
    picks = [
        btn
        for row in card.keyboard()
        for btn in row
        if btn["callback_data"].startswith("nl_pick:")
    ]
    assert len(picks) == 3


def test_a_missing_argument_hands_back_the_usage_as_a_copyable_block() -> None:
    card = nl_card.missing_args_card(
        command="space",
        usage="/space <name>",
        description="Switch the active space",
    )
    html = card.rich_html()
    assert "<h2>🧭 Missing arguments</h2>" in html
    assert "<pre><code class=\"language-text\">/space &lt;name&gt;</code></pre>" in html
    assert "Switch the active space" in html
    keyboard = card.keyboard()
    assert keyboard[0][0]["copy_text"] == {"text": "/space <name>"}
    assert keyboard[0][0]["style"] == "primary"
    assert keyboard[1][0]["callback_data"] == "nl_cancel"


def test_the_classic_projection_keeps_the_words_and_the_actions() -> None:
    card = nl_card.missing_args_card(command="space", usage="/space <name>")
    classic = card.classic_html()
    for tag in ("<h2", "<tg-button", "<footer", "<details"):
        assert tag not in classic
    assert "/space &lt;name&gt;" in classic
    assert card.keyboard()[0][0]["copy_text"] == {"text": "/space <name>"}


# -- the dispatcher --------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.rich: list[dict] = []
        self.plain: list[dict] = []

    async def send_rich_message(self, chat_id, *, html, fallback_html=None, keyboard=None, **kw):
        self.rich.append({"html": html, "keyboard": keyboard})
        return {"ok": True}

    async def send_message(self, chat_id, text, **kw):
        self.plain.append({"text": text, "keyboard": kw.get("keyboard")})
        return {"ok": True}


def _channel(recorder: _Recorder, monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message
    channel._execute_nl_pending_after_delay = _never
    channel._sniff_reminder_intent = lambda text: None
    return channel


async def _never(**kwargs):
    return None


@pytest.mark.asyncio
async def test_a_space_intent_lands_as_one_card_with_both_answers(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)
    channel._infer_nl_space_intent = lambda text: ("switch", "dev-space")

    handled = await channel._handle_natural_language_request(1, 2, "move me to dev space")

    assert handled is True
    assert len(recorder.rich) == 1
    assert recorder.plain == []
    assert "<h2>🧭 Space intent</h2>" in recorder.rich[0]["html"]
    assert recorder.rich[0]["keyboard"][0][0]["callback_data"] == "nl_yes"


@pytest.mark.asyncio
async def test_an_ambiguous_match_lands_as_a_card(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)
    channel._infer_nl_space_intent = lambda text: ("", "")
    channel._resolve_nl_command_intent = lambda text: {
        "ambiguous": True,
        "candidates": CANDIDATES,
    }

    handled = await channel._handle_natural_language_request(1, 2, "spac")

    assert handled is True
    assert len(recorder.rich) == 1
    assert "<h2>🧭 Which command?</h2>" in recorder.rich[0]["html"]


@pytest.mark.asyncio
async def test_a_command_without_arguments_lands_as_a_card(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)
    channel._infer_nl_space_intent = lambda text: ("", "")
    channel._resolve_nl_command_intent = lambda text: {
        "command": "space",
        "usage": "/space <name>",
        "missing_args": True,
        "description": "Switch space",
    }

    handled = await channel._handle_natural_language_request(1, 2, "space")

    assert handled is True
    assert len(recorder.rich) == 1
    assert "<h2>🧭 Missing arguments</h2>" in recorder.rich[0]["html"]
    assert recorder.rich[0]["keyboard"][0][0]["copy_text"] == {"text": "/space <name>"}


@pytest.mark.asyncio
async def test_a_broken_card_falls_back_to_the_sentence(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)
    channel._infer_nl_space_intent = lambda text: ("switch", "dev-space")

    import navig.telegram.card_transport as transport

    async def _boom(*args, **kwargs):
        raise RuntimeError("rich refused")

    monkeypatch.setattr(transport, "send_card", _boom)

    handled = await channel._handle_natural_language_request(1, 2, "move me to dev space")

    assert handled is True
    assert recorder.rich == []
    assert len(recorder.plain) == 1
    assert "dev-space" in recorder.plain[0]["text"]
    assert recorder.plain[0]["keyboard"][0][0]["callback_data"] == "nl_yes"
