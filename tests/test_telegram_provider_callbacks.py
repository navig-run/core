import pytest

from navig.gateway.channels.telegram_keyboards import CallbackHandler


class _FakeChannel:
    def __init__(self):
        self.api_calls = []
        self.noai_users = []
        self.provider_renders = []
        self._user_model_prefs = {}
        self.picker_calls = []
        self.persist_calls = 0

    async def _api_call(self, method, data):
        self.api_calls.append((method, data))
        return {"ok": True}

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
        self.api_calls.append(
            ("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, **kwargs})
        )
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

    async def _resolve_provider_models(self, prov_id, manifest=None):
        return ["grok-3", "grok-3-mini", "grok-2"]

    @staticmethod
    def _select_curated_tier_defaults(prov_id, models):
        return {
            "small": models[1],
            "big": models[0],
            "coder_big": models[0],
        }

    def _persist_hybrid_router_assignments(self, router_cfg):
        self.persist_calls += 1


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


@pytest.mark.asyncio
async def test_provider_callback_nvidia_nim_alias_opens_nvidia_picker():
    class _AliasChannel(_FakeChannel):
        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
        ):
            self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))

    channel = _AliasChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-2a",
        cb_data="prov_nvidia_nim",
        chat_id=111,
        message_id=211,
        user_id=311,
    )

    assert channel.picker_calls == [(111, "nvidia", 0, "s", 211)]


@pytest.mark.asyncio
async def test_provider_callback_retries_simple_picker_call_on_generic_error():
    class _RetryChannel(_FakeChannel):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
        ):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient failure")
            self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))

    channel = _RetryChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-2b",
        cb_data="prov_nvidia",
        chat_id=112,
        message_id=212,
        user_id=312,
    )

    assert channel.calls == 2
    assert channel.picker_calls == [(112, "nvidia", 0, "s", None)]


@pytest.mark.asyncio
async def test_provider_activate_uses_curated_defaults_and_persists(monkeypatch):
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    class _Manifest:
        id = "xai"
        emoji = "⚡"
        display_name = "xAI / Grok"
        models = ["grok-3", "grok-3-mini", "grok-2"]

    class _Slot:
        provider = ""
        model = ""

    class _Cfg:
        small = _Slot()
        big = _Slot()
        coder_big = _Slot()

        def slot_for_tier(self, tier):
            return {"small": self.small, "big": self.big, "coder_big": self.coder_big}[tier]

    class _Router:
        is_active = True
        cfg = _Cfg()

    class _Client:
        model_router = _Router()

    marked: list[str] = []

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: _Manifest())
    monkeypatch.setattr("navig.agent.ai_client.get_ai_client", lambda: _Client())
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )

    await handler._handle_provider_callback(
        cb_id="cb-3",
        cb_data="prov_activate_xai",
        chat_id=102,
        message_id=202,
        user_id=302,
    )

    assert _Router.cfg.small.provider == "xai"
    assert _Router.cfg.small.model == "grok-3-mini"
    assert _Router.cfg.big.model == "grok-3"
    assert _Router.cfg.coder_big.model == "grok-3"
    assert channel.persist_calls == 1
    assert marked == ["ai-provider"]


@pytest.mark.asyncio
async def test_provider_model_assignment_marks_onboarding_step(monkeypatch):
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    class _Manifest:
        id = "xai"
        emoji = "⚡"
        display_name = "xAI / Grok"

    class _Slot:
        provider = ""
        model = ""

    class _Cfg:
        small = _Slot()
        big = _Slot()
        coder_big = _Slot()

        def slot_for_tier(self, tier):
            return {"small": self.small, "big": self.big, "coder_big": self.coder_big}[tier]

    class _Router:
        is_active = True
        cfg = _Cfg()

    class _Client:
        model_router = _Router()

    marked: list[str] = []

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: _Manifest())
    monkeypatch.setattr("navig.agent.ai_client.get_ai_client", lambda: _Client())
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )

    await handler._handle_provider_model_callback(
        cb_id="cb-4",
        cb_data="pms_xai_0_s_0",
        chat_id=120,
        message_id=220,
        user_id=320,
    )

    assert _Router.cfg.small.provider == "xai"
    assert _Router.cfg.small.model == "grok-3"
    assert marked == ["ai-provider"]


@pytest.mark.asyncio
async def test_provider_callback_answered_before_picker_renders():
    """answerCallbackQuery must be sent before _show_provider_model_picker is called."""
    events: list[str] = []

    class _OrderTrackChannel(_FakeChannel):
        async def _api_call(self, method, data):
            events.append(f"api:{method}")
            return {"ok": True}

        async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
            events.append("send_message")
            return {"ok": True}

        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
        ):
            events.append("picker")
            self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))

    channel = _OrderTrackChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-order",
        cb_data="prov_nvidia",
        chat_id=200,
        message_id=300,
        user_id=400,
    )

    assert "api:answerCallbackQuery" in events
    assert "picker" in events
    answer_idx = events.index("api:answerCallbackQuery")
    picker_idx = events.index("picker")
    assert answer_idx < picker_idx, (
        f"answerCallbackQuery (pos {answer_idx}) must fire before picker (pos {picker_idx}); "
        f"events={events}"
    )


@pytest.mark.asyncio
async def test_provider_callback_double_failure_no_show_alert_toast():
    """When picker fails twice the callback must NOT emit a show_alert error toast."""
    channel = _FakeChannel()  # always raises in _show_provider_model_picker
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-toast",
        cb_data="prov_openai",
        chat_id=201,
        message_id=301,
        user_id=401,
    )

    show_alert_answers = [
        payload
        for method, payload in channel.api_calls
        if method == "answerCallbackQuery" and payload.get("show_alert") is True
    ]
    assert show_alert_answers == [], (
        f"Expected no show_alert toast on double failure, got: {show_alert_answers}"
    )
    # Fallback to providers hub should have been triggered
    assert channel.provider_renders, "Expected providers hub fallback after double failure"
