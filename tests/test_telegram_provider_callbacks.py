import pytest

from navig.gateway.channels.telegram_keyboards import CallbackHandler


class _FakeChannel:
    def __init__(self):
        self.api_calls = []
        self.noai_users = []
        self.provider_renders = []
        self._user_model_prefs = {}
        self.picker_calls = []

    async def _api_call(self, method, data):
        self.api_calls.append((method, data))
        return {"ok": True}

    def _set_one_shot_noai(self, user_id: int) -> None:
        self.noai_users.append(user_id)

    async def _handle_providers(self, chat_id, user_id=0, message_id=None):
        self.provider_renders.append((chat_id, user_id, message_id))

    async def _show_provider_model_picker(
        self,
        chat_id,
        prov_id,
        page=0,
        selected_tier="s",
        message_id=None,
    ):
        self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))
        raise RuntimeError("picker backend unavailable")


@pytest.mark.asyncio
async def test_provider_callback_noai_arms_mode_without_alert_popup():
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-1",
        cb_data="prov_noai",
        chat_id=100,
        message_id=200,
        user_id=300,
    )

    assert channel.noai_users == [300]
    assert channel.provider_renders == [(100, 300, 200)]

    answer_calls = [
        payload for method, payload in channel.api_calls if method == "answerCallbackQuery"
    ]
    assert answer_calls
    assert answer_calls[0].get("show_alert") is False


@pytest.mark.asyncio
async def test_provider_callback_picker_failure_recovers_to_provider_screen():
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-2",
        cb_data="prov_airllm",
        chat_id=101,
        message_id=201,
        user_id=301,
    )

    assert channel.picker_calls
    assert channel.provider_renders == [(101, 301, 201)]
