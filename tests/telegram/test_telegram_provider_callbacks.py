import pytest

from navig.gateway.channels.telegram_keyboards import CallbackHandler

pytestmark = pytest.mark.integration


class _FakeChannel:
    def __init__(self):
        self.api_calls = []
        self.noai_users = []
        self.provider_renders = []
        self._user_model_prefs = {}
        self.picker_calls = []
        self.persist_calls = 0
        self._telegram_nav_state: dict = {}

    def _get_navigation_state(self, chat_id: int) -> dict:
        state = self._telegram_nav_state.get(chat_id)
        if not isinstance(state, dict):
            state = {"screen_stack": ["main"], "message_id": None}
            self._telegram_nav_state[chat_id] = state
        return state

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
        show_models=False,
    ):
        self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id, show_models))
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


async def test_provider_callback_nvidia_nim_alias_opens_nvidia_picker():
    class _AliasChannel(_FakeChannel):
        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
            show_models=False,
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
            show_models=False,
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
            show_models=False,
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


async def test_pmv_tier_tab_calls_picker_with_show_models_true():
    """pmv_ tier-tab click must call _show_provider_model_picker with show_models=True."""

    class _TierChannel(_FakeChannel):
        def __init__(self):
            super().__init__()
            self.picker_show_models = []

        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
            show_models=False,
        ):
            self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))
            self.picker_show_models.append(show_models)

    channel = _TierChannel()
    handler = CallbackHandler(channel)

    await handler.handle(
        {
            "id": "cb-pmv",
            "data": "pmv_openai_b_0",
            "from": {"id": 500},
            "message": {"message_id": 600, "chat": {"id": 700}},
        }
    )

    assert channel.picker_calls, "picker must be called on pmv_ tier tab"
    assert channel.picker_calls[0][1] == "openai"
    assert channel.picker_calls[0][3] == "b"
    assert channel.picker_show_models == [True], "show_models must be True for tier tab"


async def test_pmv_tier_tab_failure_does_not_show_error_screen():
    """If _show_provider_model_picker fails on a pmv_ tap, no 'Something went wrong' screen."""

    class _FailingTierChannel(_FakeChannel):
        def __init__(self):
            super().__init__()
            self.edit_calls = []

        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
            show_models=False,
        ):
            raise RuntimeError("picker unavailable")

        async def _api_call(self, method, data):
            self.api_calls.append((method, data))
            if method == "editMessageText":
                self.edit_calls.append(data.get("text", ""))
            return {"ok": True}

    channel = _FailingTierChannel()
    handler = CallbackHandler(channel)

    await handler.handle(
        {
            "id": "cb-pmv-fail",
            "data": "pmv_openai_s_0",
            "from": {"id": 501},
            "message": {"message_id": 601, "chat": {"id": 701}},
        }
    )

    error_edits = [t for t in channel.edit_calls if "Something went wrong" in t]
    assert error_edits == [], (
        f"'Something went wrong' screen must NOT appear on pmv_ failure; got edits: {channel.edit_calls}"
    )


async def test_prov_deactivate_callback_arms_noai_and_refreshes_picker():
    """prov_deactivate_ must call _deactivate_provider, arm noai, and refresh picker."""

    class _DeactivatableChannel(_FakeChannel):
        def __init__(self):
            super().__init__()
            self.deactivated_providers: list = []
            # Override picker so it records calls without raising
            self.picker_calls = []

        def _deactivate_provider(self, prov_id: str) -> None:
            self.deactivated_providers.append(prov_id)

        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
            show_models=False,
        ):
            self.picker_calls.append(
                (chat_id, prov_id, page, selected_tier, message_id, show_models)
            )

    channel = _DeactivatableChannel()
    handler = CallbackHandler(channel)

    await handler.handle(
        {
            "id": "cb-deactivate",
            "data": "prov_deactivate_xai",
            "from": {"id": 600},
            "message": {"message_id": 700, "chat": {"id": 800}},
        }
    )

    # _deactivate_provider was called with the correct provider id
    assert "xai" in channel.deactivated_providers, (
        f"Expected _deactivate_provider('xai'), got: {channel.deactivated_providers}"
    )
    # Session-level noai was armed
    assert 600 in channel.noai_users, (
        f"Expected user 600 to be in noai_users after deactivation, got: {channel.noai_users}"
    )
    # Picker was refreshed in-place (show_models=False so tier buttons appear)
    picker_refresh = [c for c in channel.picker_calls if c[1] == "xai"]
    assert picker_refresh, "Expected picker refresh call for xai after deactivation"
    assert picker_refresh[0][5] is False, (
        "Picker refresh after deactivation must use show_models=False"
    )


async def test_prov_deactivate_answer_toast_contains_provider_name():
    """prov_deactivate_ answer toast must mention 'deactivated'."""

    class _ToastChannel(_FakeChannel):
        def _deactivate_provider(self, prov_id: str) -> None:
            pass  # no-op stub

        async def _show_provider_model_picker(self, *a, **kw):
            pass  # no-op stub

    channel = _ToastChannel()
    handler = CallbackHandler(channel)

    await handler.handle(
        {
            "id": "cb-toast-deact",
            "data": "prov_deactivate_openai",
            "from": {"id": 601},
            "message": {"message_id": 701, "chat": {"id": 801}},
        }
    )

    answer_texts = [
        payload.get("text", "")
        for method, payload in channel.api_calls
        if method == "answerCallbackQuery"
    ]
    assert any("deactivate" in t.lower() for t in answer_texts), (
        f"Expected toast with 'deactivated', got answer texts: {answer_texts}"
    )
