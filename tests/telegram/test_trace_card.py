"""The trace card: sheets, bullets, and a refresh that stays put."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from navig.telegram import trace_card as tc

NOW = datetime(2026, 10, 27, 10, 26, tzinfo=timezone.utc)


def _card(**kwargs):
    base = dict(
        bridge_online=True,
        bridge_url="127.0.0.1:11435",
        messages=12,
        sessions=3,
        tier="auto",
        host="outpost",
        voice="off",
        vault="41 entries",
        now=NOW,
    )
    base.update(kwargs)
    return tc.trace_card(**base)


def test_the_state_is_a_sheet_not_a_column_of_dashes():
    rich = _card().rich_html()
    assert "<h1>🔬 Recent trace</h1>" in rich
    assert "<table striped>" in rich
    assert "<td>🔌 Bridge Grid</td><td><b>online</b> <code>127.0.0.1:11435</code></td>" in rich
    assert "<td>🧠 Memory</td><td><b>12</b> msgs · 3 session(s)</td>" in rich
    assert "tier <code>auto</code> · host <code>outpost</code> · voice <code>off</code>" in rich
    assert "<td>🔐 Vault</td><td>41 entries</td>" in rich


def test_an_offline_bridge_says_who_is_answering_instead():
    rich = _card(bridge_online=False).rich_html()
    assert "offline</b> — the model router answers" in rich
    assert "No local bridge on this host" in rich


def test_recent_turns_are_bullets_with_a_time_and_a_side():
    rich = _card(
        recent=[
            {"role": "user", "preview": "restart the agent", "time": "10:21"},
            {"role": "assistant", "preview": "restarted, active", "time": "10:22"},
        ]
    ).rich_html()
    assert "<li><code>10:21</code> → you: restart the agent</li>" in rich
    assert "<li><code>10:22</code> ← navig: restarted, active</li>" in rich


def test_a_turn_with_no_timestamp_keeps_its_text():
    bullets = tc.recent_bullets([{"role": "user", "preview": "hello", "time": ""}])
    assert bullets[0].html == "→ you: hello"


def test_an_empty_session_is_a_sentence_not_an_empty_list():
    rich = _card(messages=0, recent=[]).rich_html()
    assert "No recent activity to show." in rich
    assert "no messages yet" in rich
    assert "<ul>" not in rich


def test_daemon_warnings_are_a_callout_and_a_capped_list():
    card = _card(warnings=["error: bridge timeout", "warning: vault slow"])
    rich = card.rich_html()
    assert "<aside>" in rich and "2 line(s) in the daemon log" in rich
    assert "<li><code>error: bridge timeout</code></li>" in rich
    capped = _card(warnings=[f"error {i}" for i in range(9)]).rich_html()
    # Five is what a phone screen tolerates; the count still tells the truth.
    assert capped.count("<li><code>error") == 5
    assert "9 line(s)" in capped


def test_the_latency_bars_stay_monospace():
    rich = _card(latency=["prep 12ms ▕█▏", "gen 800ms ▕████▏"]).rich_html()
    assert "<pre><code class=\"language-text\">prep 12ms ▕█▏\ngen 800ms ▕████▏</code></pre>" in rich


def test_the_tier_table_only_appears_in_debug_mode():
    assert "Tiers" not in _card().rich_html()
    rich = _card(tiers=[("Big", "openrouter:mimo-v2.6-flash")]).rich_html()
    assert "<caption>Tiers</caption>" in rich
    assert "<td>· Big</td><td><code>openrouter:mimo-v2.6-flash</code></td>" in rich


def test_the_buttons_are_styled_and_refresh_is_the_primary_action():
    rows = _card().keyboard()
    flat = [b for row in rows for b in row]
    assert [b["text"] for b in flat] == ["🔄 Refresh", "🧩 Providers", "🎛 Model", "✕ Close"]
    assert flat[0]["style"] == "primary"
    assert flat[-1]["style"] == "danger"
    assert [b["callback_data"] for b in flat] == [
        "trace_refresh",
        "trace_providers",
        "trace_model",
        "trace_close",
    ]


def test_the_classic_projection_carries_no_rich_only_tag():
    classic = _card(recent=[{"role": "user", "preview": "hi", "time": "10:00"}]).classic_html()
    for tag in ("<h1>", "<table", "<tg-button-row", "<aside", "<caption>"):
        assert tag not in classic


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    def _rich_messages_enabled(self) -> bool:
        return True

    async def send_rich_message(self, chat_id, *, html=None, fallback_html=None, keyboard=None, **_kw):
        self.sent.append({"html": html})
        return {"message_id": 1}

    async def edit_rich_message(self, chat_id, message_id, *, html=None, fallback_html=None, keyboard=None):
        self.edited.append({"message_id": message_id, "html": html})
        return True

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"text": text})
        return {"message_id": 2}


def test_a_refresh_redraws_the_pressed_message_instead_of_sending_a_new_one():
    from navig.telegram import card_transport

    channel = _FakeChannel()
    asyncio.run(card_transport.edit_card(channel, 42, 900, _card()))
    assert channel.edited[0]["message_id"] == 900
    assert "<table striped>" in channel.edited[0]["html"]
    assert not channel.sent
