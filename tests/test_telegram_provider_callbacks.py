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
        self.llm_mode_calls = []
        self.activation_confirms = []
        self.tier_summary_calls = []

    async def _api_call(self, method, data):
        self.api_calls.append((method, data))
        return {"ok": True}

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
        self.api_calls.append(
            ("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, **kwargs})
        )
        return {"ok": True}

    async def edit_message(
        self, chat_id, message_id, text, parse_mode=None, keyboard=None, **kwargs
    ):
        self.api_calls.append(
            ("editMessage", {"chat_id": chat_id, "message_id": message_id, "text": text})
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

    async def _show_provider_activation_confirmation(
        self,
        chat_id,
        prov_id,
        defaults,
        message_id=None,
    ):
        self.activation_confirms.append((chat_id, prov_id, defaults, message_id))

    async def _show_models_tier_summary(
        self,
        chat_id,
        prov_id,
        message_id=None,
    ):
        self.tier_summary_calls.append((chat_id, prov_id, message_id))

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

    def _update_llm_mode_router(self, provider_id, tier_models):
        self.llm_mode_calls.append((provider_id, tier_models))


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
async def test_provider_callback_activates_on_tap(monkeypatch):
    """Tapping prov_{id} immediately activates with curated defaults."""
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    class _Manifest:
        id = "openai"
        emoji = "🟢"
        display_name = "OpenAI"

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: _Manifest())

    await handler._handle_provider_callback(
        cb_id="cb-2",
        cb_data="prov_openai",
        chat_id=101,
        message_id=201,
        user_id=301,
    )

    # Should have activated (LLM mode router updated)
    assert len(channel.llm_mode_calls) == 1
    assert channel.llm_mode_calls[0][0] == "openai"
    # Should navigate directly to tier summary (not the intermediate confirmation screen)
    assert len(channel.tier_summary_calls) == 1
    assert channel.tier_summary_calls[0][1] == "openai"
    assert channel.tier_summary_calls[0][2] == 201  # message_id


@pytest.mark.asyncio
async def test_provider_callback_nvidia_nim_alias_activates(monkeypatch):
    """prov_nvidia_nim alias resolves to nvidia and activates."""
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    class _Manifest:
        id = "nvidia"
        emoji = "🟩"
        display_name = "NVIDIA"

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: _Manifest())

    await handler._handle_provider_callback(
        cb_id="cb-2a",
        cb_data="prov_nvidia_nim",
        chat_id=111,
        message_id=211,
        user_id=311,
    )

    assert len(channel.llm_mode_calls) == 1
    assert channel.llm_mode_calls[0][0] == "nvidia"
    assert len(channel.tier_summary_calls) == 1
    assert channel.tier_summary_calls[0][1] == "nvidia"


@pytest.mark.asyncio
async def test_provider_unknown_shows_unavailable(monkeypatch):
    """Unknown provider ID returns an info answer without crash."""
    channel = _FakeChannel()
    handler = CallbackHandler(channel)

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: None)

    await handler._handle_provider_callback(
        cb_id="cb-2b",
        cb_data="prov_fakeprov",
        chat_id=112,
        message_id=212,
        user_id=312,
    )

    answer_calls = [
        payload for method, payload in channel.api_calls if method == "answerCallbackQuery"
    ]
    assert answer_calls
    assert "unavailable" in answer_calls[0].get("text", "").lower()


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
    # LLM mode router must also be updated with curated defaults
    assert len(channel.llm_mode_calls) == 1
    assert channel.llm_mode_calls[0][0] == "xai"


@pytest.mark.asyncio
async def test_provider_model_assignment_marks_onboarding_step(monkeypatch):
    class _SafePickerChannel(_FakeChannel):
        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
        ):
            self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))

    channel = _SafePickerChannel()
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
    # LLM mode router must also be updated for the single tier
    assert len(channel.llm_mode_calls) == 1
    assert channel.llm_mode_calls[0] == ("xai", {"small": "grok-3"})


@pytest.mark.asyncio
async def test_provider_model_assignment_updates_llm_without_hybrid_router(monkeypatch):
    """pms_ assignment must update LLM Mode Router even when hybrid router is not active."""

    class _SafePickerChannel(_FakeChannel):
        async def _show_provider_model_picker(
            self,
            chat_id,
            prov_id,
            page=0,
            selected_tier="s",
            message_id=None,
        ):
            self.picker_calls.append((chat_id, prov_id, page, selected_tier, message_id))

    channel = _SafePickerChannel()
    handler = CallbackHandler(channel)

    class _Manifest:
        id = "xai"
        emoji = "⚡"
        display_name = "xAI / Grok"

    class _Router:
        is_active = False  # hybrid disabled
        cfg = None

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
        cb_id="cb-5",
        cb_data="pms_xai_0_b_0",
        chat_id=130,
        message_id=230,
        user_id=330,
    )

    # Should NOT block — onboarding marked and LLM router updated
    assert marked == ["ai-provider"]
    assert len(channel.llm_mode_calls) == 1
    assert channel.llm_mode_calls[0] == ("xai", {"big": "grok-3"})


@pytest.mark.asyncio
async def test_provider_callback_answered_before_activation(monkeypatch):
    """answerCallbackQuery must be sent before _show_models_tier_summary is called."""
    events: list[str] = []

    class _OrderTrackChannel(_FakeChannel):
        async def _api_call(self, method, data):
            events.append(f"api:{method}")
            return {"ok": True}

        async def send_message(self, chat_id, text, parse_mode=None, **kwargs):
            events.append("send_message")
            return {"ok": True}

        async def _show_models_tier_summary(
            self,
            chat_id,
            prov_id,
            message_id=None,
        ):
            events.append("tier_summary")
            self.tier_summary_calls.append((chat_id, prov_id, message_id))

    class _Manifest:
        id = "nvidia"
        emoji = "🟩"
        display_name = "NVIDIA"

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: _Manifest())

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
    assert "tier_summary" in events
    answer_idx = events.index("api:answerCallbackQuery")
    confirm_idx = events.index("tier_summary")
    assert answer_idx < confirm_idx, (
        f"answerCallbackQuery (pos {answer_idx}) must fire before tier_summary (pos {confirm_idx}); "
        f"events={events}"
    )


@pytest.mark.asyncio
async def test_provider_callback_activation_failure_shows_alert(monkeypatch):
    """When activation fails, a show_alert error toast should appear."""

    class _FailActivateChannel(_FakeChannel):
        async def _resolve_provider_models(self, prov_id, manifest=None):
            raise RuntimeError("network unreachable")

    class _Manifest:
        id = "openai"
        emoji = "🟢"
        display_name = "OpenAI"

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda _prov_id: _Manifest())

    channel = _FailActivateChannel()
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
    assert show_alert_answers, "Expected a show_alert toast on activation failure"
    assert "failed" in show_alert_answers[0].get("text", "").lower()


# ─── Tests for _activate_provider_with_defaults helper ────────────────────────


@pytest.mark.asyncio
async def test_activate_helper_no_models_shows_alert(monkeypatch):
    """_activate_provider_with_defaults returns False and shows alert when no models."""
    from types import SimpleNamespace

    channel = _FakeChannel()
    channel._resolve_cache = []  # type: ignore[attr-defined]

    async def _empty_models(prov_id, manifest=None):
        return []

    channel._resolve_provider_models = _empty_models  # type: ignore[method-assign]
    handler = CallbackHandler(channel)

    manifest = SimpleNamespace(
        id="fakeprov",
        display_name="Fake Provider",
        emoji="🔲",
        tier="cloud",
    )

    result = await handler._activate_provider_with_defaults("cb-x", 100, 200, "fakeprov", manifest)

    assert result is False
    alert_answers = [
        p
        for m, p in channel.api_calls
        if m == "answerCallbackQuery" and p.get("show_alert") is True
    ]
    assert alert_answers, "Should show alert when no models"
    assert "No models found" in alert_answers[0]["text"]


@pytest.mark.asyncio
async def test_activate_helper_local_llamacpp_fallback(monkeypatch):
    """_activate_provider_with_defaults uses hardcoded fallback for llamacpp with no models."""
    from types import SimpleNamespace

    channel = _FakeChannel()

    async def _no_models(prov_id, manifest=None):
        return []

    channel._resolve_provider_models = _no_models  # type: ignore[method-assign]
    handler = CallbackHandler(channel)

    manifest = SimpleNamespace(
        id="llamacpp",
        display_name="llama.cpp",
        emoji="🦙",
        tier="local",
    )

    result = await handler._activate_provider_with_defaults("cb-ll", 100, 200, "llamacpp", manifest)

    # Should succeed with fallback models
    assert result is True
    assert len(channel.llm_mode_calls) == 1
    defaults = channel.llm_mode_calls[0][1]
    # At least one known llamacpp model should be assigned
    all_vals = list(defaults.values())
    assert any("llama" in m for m in all_vals)


@pytest.mark.asyncio
async def test_prov_bridge_online_navigates_to_tier_summary():
    """prov_bridge tap when bridge is online navigates to tier summary."""
    tier_calls: list = []

    class _BridgeChannel(_FakeChannel):
        async def _probe_bridge_grid(self):
            return True, "http://localhost:1234"

        async def _show_models_tier_summary(self, chat_id, prov_id, message_id=None):
            tier_calls.append((chat_id, prov_id, message_id))

    channel = _BridgeChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-bridge",
        cb_data="prov_bridge",
        chat_id=300,
        message_id=400,
        user_id=500,
    )

    assert tier_calls == [(300, "bridge_copilot", 400)]
    answer_calls = [p for m, p in channel.api_calls if m == "answerCallbackQuery"]
    assert answer_calls
    assert "active" in answer_calls[0].get("text", "").lower()


@pytest.mark.asyncio
async def test_prov_bridge_offline_shows_alert():
    """prov_bridge tap when bridge is offline shows an alert (no navigation)."""

    class _OfflineBridgeChannel(_FakeChannel):
        async def _probe_bridge_grid(self):
            return False, "http://localhost:1234"

    channel = _OfflineBridgeChannel()
    handler = CallbackHandler(channel)

    await handler._handle_provider_callback(
        cb_id="cb-bridge-offline",
        cb_data="prov_bridge",
        chat_id=301,
        message_id=401,
        user_id=501,
    )

    alert_answers = [
        p
        for m, p in channel.api_calls
        if m == "answerCallbackQuery" and p.get("show_alert") is True
    ]
    assert alert_answers, "Expected show_alert popup when bridge offline"
    assert "offline" in alert_answers[0].get("text", "").lower()


@pytest.mark.asyncio
async def test_ms_info_error_shows_friendly_message(monkeypatch):
    """ms_info callback shows user-friendly message (not raw exception text) on error."""

    class _ModelChannel(_FakeChannel):
        async def _handle_models_command(self, chat_id, user_id, message_id=None):
            pass

    channel = _ModelChannel()
    handler = CallbackHandler(channel)

    # get_ai_client raises to simulate routing info unavailable
    monkeypatch.setattr(
        "navig.agent.ai_client.get_ai_client",
        lambda: (_ for _ in ()).throw(RuntimeError("client not configured")),
    )

    await handler._handle_model_switch(
        cb_id="cb-ms-info",
        cb_data="ms_info",
        chat_id=400,
        message_id=500,
        user_id=600,
    )

    answer_calls = [p for m, p in channel.api_calls if m == "answerCallbackQuery"]
    assert answer_calls
    answer_text = answer_calls[0].get("text", "")
    # Should NOT expose raw exception text
    assert "client not configured" not in answer_text
    # Should show a friendly fallback
    assert "unavailable" in answer_text.lower() or "⚠️" in answer_text
