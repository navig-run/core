"""
Batch tests for:
  - navig/messaging/adapters/discord_adapter.py
  - navig/messaging/adapters/whatsapp_cloud.py
  - navig/integrations/browser_orchestrator.py
  - navig/browser/orchestrator.py
  - navig/desktop/controller.py
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# navig.messaging.adapters.discord_adapter
# ---------------------------------------------------------------------------

from navig.messaging.adapters.discord_adapter import DiscordMessagingAdapter, DISCORD_AVAILABLE


class TestDiscordMessagingAdapter:
    def _make(self, **cfg):
        return DiscordMessagingAdapter(config=cfg or None)

    def test_importable(self):
        assert DiscordMessagingAdapter is not None

    def test_discord_available_is_bool(self):
        assert isinstance(DISCORD_AVAILABLE, bool)

    def test_default_construction(self):
        obj = DiscordMessagingAdapter()
        assert obj is not None

    def test_name_property(self):
        assert DiscordMessagingAdapter().name == "discord"

    def test_capabilities_list(self):
        caps = DiscordMessagingAdapter().capabilities
        assert isinstance(caps, list) and len(caps) >= 1

    def test_identity_mode(self):
        assert DiscordMessagingAdapter().identity_mode == "bot"

    def test_compliance(self):
        assert DiscordMessagingAdapter().compliance == "official"

    def test_resolve_target_returns_resolved(self):
        from navig.messaging.adapter import ResolvedTarget
        obj = DiscordMessagingAdapter()
        result = obj.resolve_target("123456789")
        assert isinstance(result, ResolvedTarget)

    def test_resolve_target_channel_id(self):
        obj = DiscordMessagingAdapter()
        result = obj.resolve_target("987654321")
        assert result.address == "987654321"

    def test_set_client(self):
        obj = DiscordMessagingAdapter()
        mock_client = MagicMock()
        obj.set_client(mock_client)
        assert obj._client is mock_client

    def test_no_discord_client_before_set(self):
        obj = DiscordMessagingAdapter()
        # _client not set yet → should be None
        assert obj._client is None


# ---------------------------------------------------------------------------
# navig.messaging.adapters.whatsapp_cloud
# ---------------------------------------------------------------------------

from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter, _GRAPH_API


class TestWhatsAppCloudAdapter:
    def _make(self, **cfg):
        return WhatsAppCloudAdapter(config=cfg or None)

    def test_importable(self):
        assert WhatsAppCloudAdapter is not None

    def test_graph_api_constant(self):
        assert "facebook" in _GRAPH_API or "graph" in _GRAPH_API.lower()

    def test_default_construction(self):
        obj = WhatsAppCloudAdapter()
        assert obj is not None

    def test_name_property(self):
        assert "whatsapp" in WhatsAppCloudAdapter().name

    def test_capabilities_list(self):
        caps = WhatsAppCloudAdapter().capabilities
        assert isinstance(caps, list) and len(caps) >= 1

    def test_identity_mode(self):
        assert WhatsAppCloudAdapter().identity_mode == "business"

    def test_compliance(self):
        assert WhatsAppCloudAdapter().compliance == "official"

    def test_construction_with_config(self):
        obj = WhatsAppCloudAdapter(config={
            "phone_number_id": "123",
            "access_token": "tok",
        })
        assert obj is not None

    def test_resolve_target_returns_resolved(self):
        from navig.messaging.adapter import ResolvedTarget
        obj = WhatsAppCloudAdapter()
        result = obj.resolve_target("+15551234567")
        assert isinstance(result, ResolvedTarget)

    def test_resolve_target_preserves_phone(self):
        obj = WhatsAppCloudAdapter()
        result = obj.resolve_target("+15551234567")
        assert "+15551234567" in result.address


# ---------------------------------------------------------------------------
# navig.integrations.browser_orchestrator
# ---------------------------------------------------------------------------

from navig.integrations.browser_orchestrator import _inject_2fa_steps, _TIMEOUT


class TestBrowserOrchestratorIntegrations:
    def test_timeout_constant(self):
        assert isinstance(_TIMEOUT, int) and _TIMEOUT > 0

    def test_inject_2fa_mutates_steps(self):
        spec = {"steps": [{"goto": {"url": "https://example.com"}}]}
        _inject_2fa_steps(spec, "123456")
        assert len(spec["steps"]) > 1

    def test_inject_2fa_adds_fill_step(self):
        spec = {"steps": []}
        _inject_2fa_steps(spec, "999888")
        # Should have added fill steps for common selectors
        fills = [s for s in spec["steps"] if "fill" in s]
        assert len(fills) >= 1

    def test_inject_2fa_adds_click_step(self):
        spec = {"steps": []}
        _inject_2fa_steps(spec, "000000")
        clicks = [s for s in spec["steps"] if "click" in s]
        assert len(clicks) >= 1

    def test_inject_2fa_adds_wait_step(self):
        spec = {"steps": []}
        _inject_2fa_steps(spec, "111222")
        waits = [s for s in spec["steps"] if "wait" in s]
        assert len(waits) >= 1

    def test_inject_2fa_preserves_existing_steps(self):
        existing = {"goto": {"url": "https://example.com"}}
        spec = {"steps": [existing]}
        _inject_2fa_steps(spec, "333444")
        assert existing in spec["steps"]

    def test_inject_2fa_code_in_fill_value(self):
        spec = {"steps": []}
        _inject_2fa_steps(spec, "MYCODE")
        fills = [s for s in spec["steps"] if "fill" in s]
        assert any(s["fill"]["value"] == "MYCODE" for s in fills)

    def test_daemon_base_returns_url(self):
        from navig.integrations.browser_orchestrator import _daemon_base
        with patch("navig.config.get_config_manager") as m:
            m.return_value.get.return_value = 7421
            url = _daemon_base()
        assert url.startswith("http://127.0.0.1:")
        assert "7421" in url


# ---------------------------------------------------------------------------
# navig.browser.orchestrator
# ---------------------------------------------------------------------------

from navig.browser.orchestrator import CortexOrchestrator


class TestCortexOrchestrator:
    def _make(self, goal="navigate to google"):
        return CortexOrchestrator(goal=goal)

    def test_importable(self):
        assert CortexOrchestrator is not None

    def test_construction(self):
        obj = self._make()
        assert obj is not None

    def test_goal_stored(self):
        obj = self._make("open settings")
        assert obj.goal == "open settings"

    def test_driver_none_by_default(self):
        obj = self._make()
        assert obj.driver is None

    def test_driver_set(self):
        mock_driver = MagicMock()
        obj = CortexOrchestrator(goal="test", driver=mock_driver)
        assert obj.driver is mock_driver

    def test_extract_json_valid(self):
        obj = self._make()
        result = obj._extract_json('{"action": "click", "target": "#btn"}')
        assert result == {"action": "click", "target": "#btn"}

    def test_extract_json_with_markdown(self):
        obj = self._make()
        result = obj._extract_json('```json\n{"action": "scroll"}\n```')
        assert isinstance(result, dict)

    def test_extract_json_invalid_returns_none(self):
        obj = self._make()
        result = obj._extract_json("not json at all !!!")
        assert result is None

    def test_extract_json_empty_returns_none(self):
        obj = self._make()
        result = obj._extract_json("")
        assert result is None

    def test_build_a11y_messages_list(self):
        obj = self._make()
        result = obj._build_a11y_messages(
            ctx={"history": []},
            a11y_text="<button>Click me</button>",
        )
        assert isinstance(result, list) and len(result) >= 1


# ---------------------------------------------------------------------------
# navig.desktop.controller
# ---------------------------------------------------------------------------

from navig.desktop.controller import DesktopConfig, DesktopController


class TestDesktopConfig:
    def test_importable(self):
        assert DesktopConfig is not None

    def test_default_construction(self):
        cfg = DesktopConfig()
        assert cfg is not None

    def test_from_config_empty(self):
        cfg = DesktopConfig.from_config({})
        assert isinstance(cfg, DesktopConfig)

    def test_from_config_with_values(self):
        cfg = DesktopConfig.from_config({"screenshot_format": "png"})
        assert isinstance(cfg, DesktopConfig)


class TestDesktopController:
    def _make(self):
        return DesktopController(DesktopConfig())

    def test_importable(self):
        assert DesktopController is not None

    def test_construction(self):
        obj = self._make()
        assert obj is not None

    def test_no_pyautogui_by_default(self):
        # _pyautogui should be None until initialized
        obj = self._make()
        assert getattr(obj, "_pyautogui", None) is None

    def test_screenshot_returns_bytes_or_none(self):
        obj = self._make()
        import navig.desktop.controller as _dc_mod
        orig = _dc_mod._pyautogui
        try:
            mock_pag = MagicMock()
            mock_pag.screenshot.return_value.tobytes.return_value = b"png"
            _dc_mod._pyautogui = mock_pag
            with patch.object(obj, "_ensure_initialized"):
                result = obj.screenshot()
            assert result is None or isinstance(result, (bytes, str))
        finally:
            _dc_mod._pyautogui = orig

    def test_click_returns_bool_or_none(self):
        obj = self._make()
        import navig.desktop.controller as _dc_mod
        orig = _dc_mod._pyautogui
        try:
            _dc_mod._pyautogui = MagicMock()
            with patch.object(obj, "_ensure_initialized"):
                result = obj.click(0, 0)
            assert result is None or isinstance(result, bool)
        finally:
            _dc_mod._pyautogui = orig
