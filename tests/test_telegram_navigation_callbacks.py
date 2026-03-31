from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_keyboards import CallbackHandler


@pytest.mark.asyncio
async def test_nav_open_targets_dispatch_to_channel_handlers(monkeypatch):
    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    called: list[tuple[str, int, int, int | None]] = []

    async def _status(self, chat_id: int, user_id: int = 0, message_id: int | None = None):
        called.append(("status", chat_id, user_id, message_id))

    async def _spaces(self, chat_id: int, message_id: int | None = None):
        called.append(("spaces", chat_id, 0, message_id))

    async def _settings_hub(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool = False,
        message_id: int | None = None,
    ):
        called.append(("settings", chat_id, user_id, message_id))

    async def _models(self, chat_id: int, user_id: int = 0, message_id: int | None = None):
        called.append(("models", chat_id, user_id, message_id))

    async def _providers(self, chat_id: int, user_id: int = 0, message_id: int | None = None):
        called.append(("providers", chat_id, user_id, message_id))

    async def _intake(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        message_id: int | None = None,
    ):
        called.append(("intake", chat_id, user_id, message_id))

    monkeypatch.setattr(TelegramChannel, "_handle_status", _status)
    monkeypatch.setattr(TelegramChannel, "_handle_spaces", _spaces)
    monkeypatch.setattr(TelegramChannel, "_handle_settings_hub", _settings_hub)
    monkeypatch.setattr(TelegramChannel, "_handle_models_command", _models)
    monkeypatch.setattr(TelegramChannel, "_handle_providers", _providers)
    monkeypatch.setattr(TelegramChannel, "_handle_intake", _intake)

    handler = CallbackHandler(channel)

    async def _noop_answer(*args, **kwargs):
        return None

    monkeypatch.setattr(handler, "_answer", _noop_answer)

    for target in ("settings", "models", "providers", "spaces", "intake", "status"):
        await handler._handle_navigation_callback(
            cb_id=f"cb-{target}",
            cb_data=f"nav:open:{target}",
            chat_id=100,
            message_id=200,
            user_id=300,
        )

    names = [item[0] for item in called]
    assert names == ["settings", "models", "providers", "spaces", "intake", "status"]


@pytest.mark.asyncio
async def test_nav_open_does_not_mark_canonical_onboarding_steps(monkeypatch):
    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(TelegramChannel, "navigateTo", _noop)

    marked: list[str] = []

    def _mark(step_id: str, navig_dir=None):
        marked.append(step_id)
        return True

    monkeypatch.setattr("navig.commands.init.mark_chat_onboarding_step_completed", _mark)

    handler = CallbackHandler(channel)

    async def _noop_answer(*args, **kwargs):
        return None

    monkeypatch.setattr(handler, "_answer", _noop_answer)

    for target in ("providers", "intake", "status", "models"):
        await handler._handle_navigation_callback(
            cb_id=f"cb-{target}",
            cb_data=f"nav:open:{target}",
            chat_id=1,
            message_id=2,
            user_id=3,
        )

    assert marked == []


@pytest.mark.asyncio
async def test_provider_picker_delegate_accepts_decorated_signature(monkeypatch):
    channel = TelegramChannel(
        bot_token="123:FAKE",
        allowed_users=[42],
        on_message=lambda *args, **kwargs: None,
    )

    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    captured = {}

    async def _wrapped_like_decorator(
        self,
        update_or_chat_id,
        prov_id,
        page=0,
        selected_tier="s",
        message_id=None,
    ):
        captured["chat_id"] = update_or_chat_id
        captured["prov_id"] = prov_id
        captured["page"] = page
        captured["selected_tier"] = selected_tier
        captured["message_id"] = message_id

    monkeypatch.setattr(
        TelegramCommandsMixin,
        "_show_provider_model_picker",
        _wrapped_like_decorator,
    )

    await channel._show_provider_model_picker(
        chat_id=101,
        prov_id="airllm",
        page=2,
        selected_tier="b",
        message_id=303,
    )

    assert captured == {
        "chat_id": 101,
        "prov_id": "airllm",
        "page": 2,
        "selected_tier": "b",
        "message_id": 303,
    }
