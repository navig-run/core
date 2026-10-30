"""The monitoring cards: meters, sheets, and the seam that sends them."""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
from navig.telegram import monitor_card as mc
from navig.telegram.card_doc import Raw


class _FakeChannel:
    """Just enough channel for `_send_monitor_card` to make its decision."""

    def __init__(self, *, rich: bool = True) -> None:
        self.rich = rich
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.text_edits: list[dict] = []
        if rich:
            self._rich_messages_enabled = lambda: True

    async def send_rich_message(self, chat_id, *, html=None, fallback_html=None, **kw):
        self.sent.append({"kind": "rich", "chat_id": chat_id, "html": html, "fallback": fallback_html})
        return {"message_id": 11}

    async def edit_rich_message(self, chat_id, message_id, *, html=None, fallback_html=None, **kw):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "html": html})
        return True

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"kind": "text", "chat_id": chat_id, "text": text, **kw})
        return {"message_id": 12}

    async def edit_message(self, chat_id, message_id, text, **kw):
        self.text_edits.append({"message_id": message_id, "text": text})
        return {}

    async def send_command_output(self, chat_id, text, **kw):
        self.sent.append({"kind": "command_output", "chat_id": chat_id, "text": text, **kw})
        return {"message_id": 13}


def _disk_card(meters: tuple[mc.Meter, ...] | None = None):
    return mc.meter_card(
        "💽",
        "Disk Usage",
        meta="localhost · 10:59 UTC",
        meters=meters
        if meters is not None
        else (
            mc.Meter("C:\\", 71.2, 418.6, 587.9),
            mc.Meter("/", 88.0, 92.1, 104.5, note="root"),
        ),
        notes=[("Updated", "<i>10:59 UTC</i>")],
        verdict="Nothing is close to full.",
        footer="Refresh after a cleanup.",
        buttons=[("🔄 Refresh", "slash:disk"), ("🧠 Memory", "slash:memory"), ("⚡ CPU", "slash:cpu")],
    )


def test_a_meter_is_a_bar_and_the_numbers_behind_it():
    card = _disk_card()
    rich = card.rich_html()
    assert "🟡 <b>C:\\</b>" in rich
    assert "<code>██████████░░░░</code> <b>71%</b>" in rich
    assert "418.6 / 587.9 GB" in rich
    assert "🟢 <b>/</b>" not in rich  # 88 % is red, not green
    assert "92.1 / 104.5 GB · root" in rich


def test_the_thresholds_are_the_classic_ones():
    assert mc.status_icon(10) == "🟢"
    assert mc.status_icon(60) == "🟡"
    assert mc.status_icon(79.9) == "🟡"
    assert mc.status_icon(80) == "🔴"
    assert mc.bar(0) == "░" * 14
    assert mc.bar(100) == "█" * 14
    assert mc.bar(50) == "█" * 7 + "░" * 7


def test_the_verdict_and_the_footer_are_part_of_the_card():
    rich = _disk_card().rich_html()
    assert "<p><i>Nothing is close to full.</i></p>" in rich
    assert "<footer>Refresh after a cleanup.</footer>" in rich


def test_the_actions_ride_inside_the_bubble_two_per_row():
    card = _disk_card()
    rich = card.rich_html()
    assert rich.count("<tg-button-row") == 2
    assert 'data="slash:disk"' in rich and 'data="slash:cpu"' in rich
    assert card.keyboard() == [
        [
            {"text": "🔄 Refresh", "callback_data": "slash:disk"},
            {"text": "🧠 Memory", "callback_data": "slash:memory"},
        ],
        [{"text": "⚡ CPU", "callback_data": "slash:cpu"}],
    ]


def test_a_snap_image_is_not_a_volume_to_worry_about():
    # A squashfs snap is 100 % full by construction; a card that turns red for
    # it buries the volume that actually needs cleaning.
    assert mc.is_measurable("/", "ext4")
    assert mc.is_measurable("/mnt/wd", "ext4")
    assert not mc.is_measurable("/snap/core22/2437", "squashfs")
    assert not mc.is_measurable("/run/user/1000", "tmpfs")
    assert not mc.is_measurable("/proc", "")


def test_the_daemon_is_named_by_the_unit_that_actually_runs_it():
    # The brain runs `navig-agent`, not `navig-daemon`. Asking only for the latter
    # made a healthy daemon read "🔴 inactive" on the card it was serving.
    states = mc.parse_units(
        "Id=navig-agent.service\nActiveState=active\n"
        "ActiveEnterTimestamp=Mon 2026-10-27 09:12:03 CET\n"
        "\n"
        "Id=navig-daemon.service\nActiveState=inactive\n"
        "ActiveEnterTimestamp=\n"
    )
    assert mc.daemon_fact(states) == (True, "active as navig-agent, since 09:12:03")


def test_a_daemon_that_reports_nothing_active_is_named_honestly():
    assert mc.daemon_fact({"navig-agent.service": ("inactive", "")}) is None
    assert mc.daemon_fact({}) is None


def test_a_bridge_that_does_not_exist_is_not_a_fault():
    # An install served entirely by cloud providers has no local bridge at all:
    # `get_llm_port()` returns None. That must read as unused, never as red.
    assert mc.bridge_fact(None, False) == "not used — no local bridge configured"
    assert "🔴" not in mc.bridge_fact(None, False)


def test_a_configured_bridge_that_does_not_answer_is_red():
    assert mc.bridge_fact(42070, False) == "🔴 offline · port 42070"
    assert mc.bridge_fact(42070, True) == "🟢 online · port 42070"


def test_a_card_with_nothing_measured_still_names_the_reason():
    card = mc.meter_card("💽", "Disk Usage", verdict="psutil is not installed.")
    rich = card.rich_html()
    assert "<table" not in rich
    assert "psutil is not installed." in rich


def test_a_sheet_card_carries_labelled_facts_and_a_pull_quote():
    card = mc.sheet_card(
        "🕐",
        "Server Uptime",
        meta="localhost",
        sheet=[("Up", Raw("<b>3 days</b>")), ("Boot", "2026-10-24 08:12 UTC")],
        verdict="No restart in three days.",
        buttons=[("🔄 Refresh", "slash:uptime")],
    )
    rich = card.rich_html()
    assert "<td>Up</td><td><b>3 days</b></td>" in rich
    assert "<td>Boot</td><td>2026-10-24 08:12 UTC</td>" in rich
    assert "<aside>No restart in three days.</aside>" in rich
    assert 'data="slash:uptime"' in rich


def test_a_process_table_keeps_the_columns_and_the_limit():
    rows = [(str(i), f"proc{i}", float(i)) for i in range(12)]
    table = mc.process_table(rows, limit=3)
    html = table.__class__.__name__
    body = table.html() if hasattr(table, "html") else ""
    assert html == "Table"
    if body:
        assert "proc0" in body and "proc3" not in body


def test_a_keyboard_is_flattened_for_the_card_body():
    pairs = mc.buttons_from_keyboard(
        [
            [{"text": "A", "callback_data": "a"}, {"text": "B"}],
            [{"text": "C", "callback_data": "c"}],
        ]
    )
    assert pairs == [("A", "a"), ("C", "c")]


@pytest.mark.asyncio
async def test_a_measured_card_goes_out_rich_with_the_text_as_fallback():
    channel = _FakeChannel(rich=True)
    card = _disk_card()
    await TelegramCommandsMixin._send_monitor_card(
        channel, 42, "💽 <b>Disk Usage</b>", [], command="disk", card=card
    )
    assert channel.sent[0]["kind"] == "rich"
    assert "<table striped>" in channel.sent[0]["html"]
    assert channel.sent[0]["fallback"] == "💽 <b>Disk Usage</b>"


@pytest.mark.asyncio
async def test_a_refresh_edits_the_same_message_and_keeps_it_rich():
    channel = _FakeChannel(rich=True)
    await TelegramCommandsMixin._send_monitor_card(
        channel, 42, "💽 <b>Disk Usage</b>", [], message_id=77, card=_disk_card()
    )
    assert channel.edited and channel.edited[0]["message_id"] == 77
    assert "<h1>💽 Disk Usage</h1>" in channel.edited[0]["html"]
    assert not channel.text_edits


@pytest.mark.asyncio
async def test_without_a_card_the_seam_still_sends_the_old_text():
    channel = _FakeChannel(rich=True)
    await TelegramCommandsMixin._send_monitor_card(
        channel, 42, "💽 <b>Disk</b>", [], command="disk"
    )
    assert channel.sent[0]["kind"] == "command_output"
    assert channel.sent[0]["text"] == "💽 <b>Disk</b>"


@pytest.mark.asyncio
async def test_a_channel_without_rich_messages_gets_the_text():
    channel = _FakeChannel(rich=False)
    channel.send_rich_message = None  # the method exists but is unusable
    await TelegramCommandsMixin._send_monitor_card(
        channel, 42, "💽 <b>Disk</b>", [], card=_disk_card()
    )
    assert channel.sent[0]["kind"] == "text"
    assert channel.sent[0]["text"] == "💽 <b>Disk</b>"
