"""Tests for _show_provider_model_picker pagination fix (issue #58).

PAGE_SIZE was previously max(1, len(models)), which dumped all models onto
a single page.  For providers with 100+ models this created oversized
Telegram keyboards that exceeded API limits and triggered the
"Couldn't open … picker" error cascade.

The fix: fixed PAGE_SIZE = 20 with ⬅️ Prev / Next ➡️ navigation buttons when
total_pages > 1.
"""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.integration


class _MinimalChannel:
    """Minimal stub that satisfies _show_provider_model_picker's dependencies."""

    def __init__(self):
        self.sent: list[dict] = []  # list of {"text": ..., "keyboard": ...}

    async def send_message(self, chat_id, text, keyboard=None, parse_mode=None, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    async def edit_message(self, *args, **kwargs):
        # Signal that edit is not available so the method falls back to send_message
        raise Exception("edit not supported in this test")

    async def _api_call(self, method, data):
        return {"ok": True}


def _build_channel_with_method():
    """Return a channel instance that has _show_provider_model_picker bound to it."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    class _Chan(_MinimalChannel, TelegramCommandsMixin):
        pass

    return _Chan()


async def test_picker_pagination_limits_to_20_per_page():
    """Page 0 of a large model list must contain at most 20 model buttons."""
    channel = _build_channel_with_method()
    many_models = [f"provider/model-{i:03d}" for i in range(50)]

    with (
        patch.object(
            type(channel),
            "_resolve_provider_models",
            new=AsyncMock(return_value=many_models),
        ),
        patch("navig.providers.registry._INDEX", {"testprov": None}),
        patch(
            "navig.agent.ai_client.get_ai_client",
            side_effect=Exception("no ai client"),
        ),
    ):
        await channel._show_provider_model_picker(1, prov_id="testprov", page=0, message_id=None, show_models=True)

    assert channel.sent, "Expected at least one message to be sent"
    keyboard = channel.sent[0]["keyboard"]
    assert keyboard is not None

    # Count rows that correspond to individual model buttons (callback_data starts with "pms_")
    model_rows = [
        row
        for row in keyboard
        if any(btn.get("callback_data", "").startswith("pms_") for btn in row)
    ]
    assert len(model_rows) <= 20, f"Expected at most 20 model rows on page 0, got {len(model_rows)}"


async def test_picker_pagination_adds_next_button_for_large_list():
    """When models exceed one page a 'Next ➡️' navigation button must appear."""
    channel = _build_channel_with_method()
    many_models = [f"provider/model-{i:03d}" for i in range(30)]

    with (
        patch.object(
            type(channel),
            "_resolve_provider_models",
            new=AsyncMock(return_value=many_models),
        ),
        patch("navig.providers.registry._INDEX", {"testprov2": None}),
        patch(
            "navig.agent.ai_client.get_ai_client",
            side_effect=Exception("no ai client"),
        ),
    ):
        await channel._show_provider_model_picker(1, prov_id="testprov2", page=0, message_id=None, show_models=True)

    keyboard = channel.sent[0]["keyboard"]
    all_buttons = [btn for row in keyboard for btn in row]
    next_buttons = [b for b in all_buttons if "Next" in b.get("text", "")]
    assert next_buttons, "Expected a 'Next ➡️' navigation button for paginated list"


async def test_picker_pagination_prev_button_on_non_first_page():
    """Page > 0 must include a '⬅️ Prev' navigation button."""
    channel = _build_channel_with_method()
    many_models = [f"provider/model-{i:03d}" for i in range(30)]

    with (
        patch.object(
            type(channel),
            "_resolve_provider_models",
            new=AsyncMock(return_value=many_models),
        ),
        patch("navig.providers.registry._INDEX", {"testprov3": None}),
        patch(
            "navig.agent.ai_client.get_ai_client",
            side_effect=Exception("no ai client"),
        ),
    ):
        await channel._show_provider_model_picker(1, prov_id="testprov3", page=1, message_id=None, show_models=True)

    keyboard = channel.sent[0]["keyboard"]
    all_buttons = [btn for row in keyboard for btn in row]
    prev_buttons = [b for b in all_buttons if "Prev" in b.get("text", "")]
    assert prev_buttons, "Expected a '⬅️ Prev' navigation button on page 1"


async def test_picker_no_pagination_for_small_list():
    """When models fit on one page no Prev/Next buttons should appear."""
    channel = _build_channel_with_method()
    small_models = [f"provider/model-{i}" for i in range(5)]

    with (
        patch.object(
            type(channel),
            "_resolve_provider_models",
            new=AsyncMock(return_value=small_models),
        ),
        patch("navig.providers.registry._INDEX", {"testprov4": None}),
        patch(
            "navig.agent.ai_client.get_ai_client",
            side_effect=Exception("no ai client"),
        ),
    ):
        await channel._show_provider_model_picker(1, prov_id="testprov4", page=0, message_id=None, show_models=True)

    keyboard = channel.sent[0]["keyboard"]
    all_buttons = [btn for row in keyboard for btn in row]
    nav_buttons = [
        b for b in all_buttons if "Next" in b.get("text", "") or "Prev" in b.get("text", "")
    ]
    assert nav_buttons == [], f"Expected no pagination buttons for 5 models, got: {nav_buttons}"
