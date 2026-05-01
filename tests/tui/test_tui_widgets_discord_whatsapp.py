"""
Batch 124: TUI widgets (check_row, brand_hero, step_indicator, summary_panel,
status_row) + discord_adapter + whatsapp_cloud messaging adapters.

TUI widgets import `textual` which is not installed — we inject stubs into
sys.modules *before* importing any widget module.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Inject textual stubs (textual not installed in this env)
# ---------------------------------------------------------------------------

class _MockStatic:
    """Minimal Static stand-in that records update() calls."""

    DEFAULT_CSS = ""

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        self._content = content
        self._last_update: str = content

    def update(self, text: str) -> None:
        self._last_update = text

    def refresh(self) -> None:
        pass


class _MockReactive:
    """Descriptor-free reactive stub (value stored only at class level for init)."""

    def __init__(self, default: Any) -> None:
        self._default = default


_textual_stub = MagicMock()
_widgets_stub = MagicMock()
_widgets_stub.Static = _MockStatic
_reactive_stub = MagicMock()
_reactive_stub.reactive = _MockReactive

import importlib.util as _importlib_util
import pathlib as _pathlib

_REPO_ROOT = _pathlib.Path(__file__).resolve().parents[2]

# Register our typed stubs so widget files find them when they do
# `from textual.widgets import Static` / `from textual.reactive import reactive`.
sys.modules["textual"] = _textual_stub
sys.modules["textual.widgets"] = _widgets_stub
sys.modules["textual.reactive"] = _reactive_stub


def _load_widget_file(relpath: str, mod_name: str):
    """
    Load a widget module DIRECTLY from its .py file, bypassing navig.tui __init__.

    This avoids the textual chain: navig.tui.__init__ → navig.tui.app → textual.app
    and all deep textual sub-imports that we haven't mocked.
    """
    spec = _importlib_util.spec_from_file_location(mod_name, _REPO_ROOT / relpath)
    mod = _importlib_util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_check_row_mod = _load_widget_file("navig/tui/widgets/check_row.py", "_w_check_row")
_brand_hero_mod = _load_widget_file("navig/tui/widgets/brand_hero.py", "_w_brand_hero")
_step_ind_mod = _load_widget_file("navig/tui/widgets/step_indicator.py", "_w_step_indicator")
_summary_mod = _load_widget_file("navig/tui/widgets/summary_panel.py", "_w_summary_panel")
_status_row_mod = _load_widget_file("navig/tui/widgets/status_row.py", "_w_status_row")

CheckRow = _check_row_mod.CheckRow
BrandHero = _brand_hero_mod.BrandHero
StepIndicator = _step_ind_mod.StepIndicator
SummaryPanel = _summary_mod.SummaryPanel
StatusRow = _status_row_mod.StatusRow

# ---------------------------------------------------------------------------
# Messaging adapters
# ---------------------------------------------------------------------------
from navig.messaging.adapters.discord_adapter import (
    DiscordMessagingAdapter,
    DISCORD_AVAILABLE,
)
from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter
from navig.messaging.adapter import DeliveryStatus, InboundEvent, Thread

# ===========================================================================
# TUI: CheckRow
# ===========================================================================


class TestCheckRow:
    def _make(self, label: str = "Test check") -> CheckRow:
        row = CheckRow(label)
        return row

    def test_initial_state_pending(self):
        row = self._make("Disk space")
        assert row._state == "pending"
        assert row._hint == ""
        assert row._label == "Disk space"

    def test_set_pass_changes_state(self):
        row = self._make("SSH")
        row.set_pass()
        assert row._state == "pass"
        assert row._hint == ""

    def test_set_pass_renders_check_in_text(self):
        row = self._make("SSH")
        row.set_pass()
        assert "pass" in row._state
        # _refresh_render calls update() -> _last_update contains label
        assert "SSH" in row._last_update

    def test_set_fail_with_hint(self):
        row = self._make("Port 22")
        row.set_fail("Run: sudo systemctl start sshd")
        assert row._state == "fail"
        assert row._hint == "Run: sudo systemctl start sshd"
        assert "Port 22" in row._last_update
        assert "systemctl" in row._last_update  # hint included

    def test_set_fail_without_hint(self):
        row = self._make("DNS")
        row.set_fail()
        assert row._hint == ""
        assert "DNS" in row._last_update

    def test_set_pending_clears_hint(self):
        row = self._make("X")
        row.set_fail("some hint")
        row.set_pending()
        assert row._state == "pending"
        assert row._hint == ""

    def test_refresh_render_icon_map_keys(self):
        row = self._make("Y")
        for state in ("pending", "pass", "fail"):
            row._state = state
            row._refresh_render()
            assert row._label in row._last_update


# ===========================================================================
# TUI: BrandHero
# ===========================================================================


class TestBrandHero:
    def test_initial_content_empty(self):
        hero = BrandHero()
        assert hero._content == ""

    def test_render_returns_content(self):
        hero = BrandHero()
        assert hero.render() == ""

    def test_set_text_updates_content(self):
        hero = BrandHero()
        hero.set_text("NAVIG v3")
        assert hero._content == "NAVIG v3"
        assert hero.render() == "NAVIG v3"

    def test_set_text_overwrite(self):
        hero = BrandHero()
        hero.set_text("first")
        hero.set_text("second")
        assert hero.render() == "second"


# ===========================================================================
# TUI: StepIndicator
# ===========================================================================


class TestStepIndicator:
    def _make(self, current: int = 0, total: int = 5) -> StepIndicator:
        ind = StepIndicator()
        # Manually set reactive attributes as instance attrs
        ind.current_step = current
        ind.total_steps = total
        ind.step_labels = ["Identity", "Provider", "Runtime", "Packs", "Shell", "Integrations"]
        return ind

    def test_render_returns_string(self):
        ind = self._make(0, 5)
        result = ind.render()
        assert isinstance(result, str)

    def test_render_step_one(self):
        ind = self._make(0, 5)
        result = ind.render()
        assert "Step 1/5" in result
        assert "Identity" in result

    def test_render_middle_step(self):
        ind = self._make(2, 5)
        result = ind.render()
        assert "Step 3/5" in result
        assert "Runtime" in result

    def test_render_percentage(self):
        ind = self._make(4, 5)
        result = ind.render()
        # 5/5 = 100%
        assert "100%" in result

    def test_render_last_step(self):
        ind = self._make(5, 6)
        result = ind.render()
        assert "Step 6/6" in result


# ===========================================================================
# TUI: SummaryPanel
# ===========================================================================


class _MockCfg:
    profile_name = "ops-user"
    ai_provider = "openai"
    local_runtime_enabled = True
    capability_packs = ["core", "search"]
    shell_integration = True
    git_hooks = False
    telemetry = True


class TestSummaryPanel:
    def test_initial_status_unbound(self):
        panel = SummaryPanel(_MockCfg())
        assert panel._status == "unbound"

    def test_set_status(self):
        panel = SummaryPanel(_MockCfg())
        panel.set_status("active")
        assert panel._status == "active"

    def test_refresh_from_updates_cfg(self):
        cfg1 = _MockCfg()
        panel = SummaryPanel(cfg1)
        cfg2 = _MockCfg()
        cfg2.profile_name = "other-user"
        panel.refresh_from(cfg2)
        assert panel._cfg is cfg2

    def test_render_contains_profile_name(self):
        panel = SummaryPanel(_MockCfg())
        output = panel.render()
        assert "ops-user" in output

    def test_render_contains_provider(self):
        panel = SummaryPanel(_MockCfg())
        output = panel.render()
        assert "openai" in output

    def test_render_active_status(self):
        panel = SummaryPanel(_MockCfg())
        panel.set_status("active")
        output = panel.render()
        assert "active" in output

    def test_render_no_packs(self):
        cfg = _MockCfg()
        cfg.capability_packs = []
        panel = SummaryPanel(cfg)
        output = panel.render()
        assert isinstance(output, str)


# ===========================================================================
# TUI: StatusRow
# ===========================================================================


class _MockBadge:
    color = "green"
    symbol = "✓"
    status = "ok"
    detail = "All good"
    deep_link = ""
    label = "SSH"


class TestStatusRow:
    def test_badge_property(self):
        badge = _MockBadge()
        row = StatusRow(badge)
        assert row.badge is badge

    def test_deep_link_property(self):
        badge = _MockBadge()
        badge.deep_link = "/settings/ssh"
        row = StatusRow(badge)
        assert row.deep_link == "/settings/ssh"

    def test_update_badge_ok_status(self):
        badge = _MockBadge()
        row = StatusRow(badge)
        assert "SSH" in row._last_update

    def test_update_badge_warn_with_deep_link(self):
        badge = _MockBadge()
        badge.status = "warn"
        badge.deep_link = "/settings/firewall"
        row = StatusRow(badge)
        assert "SSH" in row._last_update
        # CTA hint appended
        assert "firewall" in row._last_update

    def test_update_badge_error_prefix(self):
        badge = _MockBadge()
        badge.status = "error"
        badge.detail = "Connection refused"
        row = StatusRow(badge)
        assert "SSH" in row._last_update

    def test_update_badge_missing(self):
        badge = _MockBadge()
        badge.status = "missing"
        badge.detail = "Loading..."
        badge.deep_link = ""
        row = StatusRow(badge)
        assert "SSH" in row._last_update

    def test_update_badge_missing_with_link(self):
        badge = _MockBadge()
        badge.status = "missing"
        badge.detail = ""
        badge.deep_link = "/settings/api"
        row = StatusRow(badge)
        assert "api" in row._last_update


# ===========================================================================
# Discord Messaging Adapter
# ===========================================================================


class TestDiscordMessagingAdapter:
    def test_name(self):
        adapter = DiscordMessagingAdapter()
        assert adapter.name == "discord"

    def test_capabilities(self):
        adapter = DiscordMessagingAdapter()
        caps = adapter.capabilities
        assert "text" in caps
        assert "threads" in caps

    def test_identity_mode(self):
        adapter = DiscordMessagingAdapter()
        assert adapter.identity_mode == "bot"

    def test_compliance(self):
        adapter = DiscordMessagingAdapter()
        assert adapter.compliance == "official"

    def test_init_with_config(self):
        adapter = DiscordMessagingAdapter({"bot_token": "test-token"})
        assert adapter._bot_token == "test-token"

    def test_init_without_config(self):
        adapter = DiscordMessagingAdapter()
        assert adapter._bot_token == ""
        assert adapter._client is None

    def test_set_client(self):
        adapter = DiscordMessagingAdapter()
        mock_client = MagicMock()
        adapter.set_client(mock_client)
        assert adapter._client is mock_client

    def test_get_client_raises_when_none(self):
        adapter = DiscordMessagingAdapter()
        with pytest.raises(RuntimeError, match="not initialised"):
            adapter._get_client()

    def test_get_client_returns_client_when_set(self):
        adapter = DiscordMessagingAdapter()
        mock_client = MagicMock()
        adapter.set_client(mock_client)
        assert adapter._get_client() is mock_client

    def test_resolve_target_with_prefix(self):
        adapter = DiscordMessagingAdapter()
        target = adapter.resolve_target("discord:987654321")
        assert target.adapter == "discord"
        assert target.address == "987654321"

    def test_resolve_target_without_prefix(self):
        adapter = DiscordMessagingAdapter()
        target = adapter.resolve_target("123456789")
        assert target.address == "123456789"

    def test_receive_webhook(self):
        adapter = DiscordMessagingAdapter()
        payload = {
            "channel_id": "chan-99",
            "author_id": "user-42",
            "content": "Hello bot!",
        }
        event = asyncio.run(adapter.receive_webhook(payload))
        assert isinstance(event, InboundEvent)
        assert event.adapter == "discord"
        assert event.remote_conversation_id == "chan-99"
        assert event.sender == "user-42"
        assert event.text == "Hello bot!"

    def test_receive_webhook_empty(self):
        adapter = DiscordMessagingAdapter()
        event = asyncio.run(adapter.receive_webhook({}))
        assert event.adapter == "discord"
        assert event.text == ""

    def test_send_message_discord_unavailable(self):
        """When DISCORD_AVAILABLE is False, send returns a failure receipt."""
        import navig.messaging.adapters.discord_adapter as _mod
        original = _mod.DISCORD_AVAILABLE
        try:
            _mod.DISCORD_AVAILABLE = False
            adapter = DiscordMessagingAdapter()
            receipt = asyncio.run(adapter.send_message("123", "hello"))
            assert receipt.ok is False
            assert "not installed" in (receipt.error or "")
        finally:
            _mod.DISCORD_AVAILABLE = original

    def test_ingest_event_calls_thread_store(self):
        adapter = DiscordMessagingAdapter()
        mock_store = MagicMock()
        mock_thread = Thread(id=1, adapter="discord", remote_conversation_id="chan-1")
        mock_store.get_or_create.return_value = mock_thread

        event = InboundEvent(
            adapter="discord",
            remote_conversation_id="chan-1",
            sender="user-1",
            text="hi",
        )
        with patch("navig.store.threads.get_thread_store", return_value=mock_store):
            asyncio.run(adapter.ingest_event(event))

        mock_store.get_or_create.assert_called_once_with("discord", "chan-1")
        mock_store.touch.assert_called_once_with(1)


# ===========================================================================
# WhatsApp Cloud Adapter
# ===========================================================================


class TestWhatsAppCloudAdapter:
    def test_name(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter.name == "whatsapp"

    def test_capabilities(self):
        adapter = WhatsAppCloudAdapter()
        caps = adapter.capabilities
        assert "text" in caps
        assert "media" in caps

    def test_identity_mode(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter.identity_mode == "business"

    def test_compliance(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter.compliance == "official"

    def test_init_with_config(self):
        cfg = {
            "phone_number_id": "12345",
            "access_token": "tok",
            "api_version": "v19.0",
        }
        adapter = WhatsAppCloudAdapter(cfg)
        assert adapter._phone_number_id == "12345"
        assert adapter._access_token == "tok"
        assert adapter._api_version == "v19.0"

    def test_resolve_target_with_prefix(self):
        adapter = WhatsAppCloudAdapter()
        target = adapter.resolve_target("whatsapp:+33612345678")
        assert target.adapter == "whatsapp_cloud"
        assert target.address == "+33612345678"

    def test_resolve_target_without_prefix(self):
        adapter = WhatsAppCloudAdapter()
        target = adapter.resolve_target("+12125551234")
        assert target.address == "+12125551234"

    def test_resolve_target_strips_whitespace(self):
        adapter = WhatsAppCloudAdapter()
        target = adapter.resolve_target("whatsapp:  +44700000000  ")
        assert target.address == "+44700000000"

    def _make_wa_payload(self, from_num: str = "+1234", body: str = "hello") -> dict:
        return {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": from_num,
                            "text": {"body": body},
                        }]
                    }
                }]
            }]
        }

    def test_receive_webhook_normal(self):
        adapter = WhatsAppCloudAdapter()
        payload = self._make_wa_payload("+33612345678", "Bonjour!")
        event = asyncio.run(adapter.receive_webhook(payload))
        assert isinstance(event, InboundEvent)
        assert event.adapter == "whatsapp_cloud"
        assert event.remote_conversation_id == "+33612345678"
        assert event.sender == "+33612345678"
        assert event.text == "Bonjour!"

    def test_receive_webhook_empty_payload(self):
        adapter = WhatsAppCloudAdapter()
        event = asyncio.run(adapter.receive_webhook({}))
        assert event.adapter == "whatsapp_cloud"
        # empty payload: entry/changes/value/messages all default to {}/{}/[{}]
        # so from="" not "unknown" (no IndexError raised)
        assert event.remote_conversation_id == ""
        assert event.sender == ""
        assert event.text == ""

    def test_receive_webhook_missing_messages_key(self):
        """Malformed: value has no 'messages'."""
        adapter = WhatsAppCloudAdapter()
        payload = {"entry": [{"changes": [{"value": {}}]}]}
        event = asyncio.run(adapter.receive_webhook(payload))
        # Empty messages list → msg = {}, from = "" 
        assert isinstance(event, InboundEvent)
        assert event.text == ""

    def test_receive_webhook_preserves_raw(self):
        adapter = WhatsAppCloudAdapter()
        payload = self._make_wa_payload("+1", "test")
        event = asyncio.run(adapter.receive_webhook(payload))
        assert event.raw is payload

    def test_ingest_event_calls_thread_store(self):
        adapter = WhatsAppCloudAdapter()
        mock_store = MagicMock()
        mock_thread = Thread(id=7, adapter="whatsapp_cloud", remote_conversation_id="+1")
        mock_store.get_or_create.return_value = mock_thread

        event = InboundEvent(
            adapter="whatsapp_cloud",
            remote_conversation_id="+1",
            sender="+1",
            text="ping",
        )
        with patch("navig.store.threads.get_thread_store", return_value=mock_store):
            asyncio.run(adapter.ingest_event(event))

        mock_store.get_or_create.assert_called_once_with("whatsapp_cloud", "+1")
        mock_store.touch.assert_called_once_with(7)

    def test_get_session_raises_import_error_without_aiohttp(self):
        """_get_session raises ImportError when aiohttp is not available."""
        adapter = WhatsAppCloudAdapter()
        with patch.dict(sys.modules, {"aiohttp": None}):
            with pytest.raises(ImportError, match="aiohttp required"):
                asyncio.run(adapter._get_session())

    def test_close_noop_when_no_session(self):
        adapter = WhatsAppCloudAdapter()
        # Should not raise
        asyncio.run(adapter.close())
        assert adapter._session is None

    def test_close_calls_session_close(self):
        adapter = WhatsAppCloudAdapter()
        mock_session = AsyncMock()
        adapter._session = mock_session
        asyncio.run(adapter.close())
        mock_session.close.assert_called_once()
        assert adapter._session is None

    def test_send_message_success(self):
        """send_message returns success receipt when API responds 200 with messages."""
        adapter = WhatsAppCloudAdapter({
            "phone_number_id": "pid",
            "access_token": "tok",
        })

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"messages": [{"id": "msg123"}]})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        adapter._session = mock_session

        receipt = asyncio.run(adapter.send_message("+123", "Hello"))
        assert receipt.ok is True
        assert receipt.message_id == "msg123"
        assert receipt.status == DeliveryStatus.SENT

    def test_send_message_api_error(self):
        """send_message returns failure when API returns error."""
        adapter = WhatsAppCloudAdapter({
            "phone_number_id": "pid",
            "access_token": "tok",
        })

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value={"error": {"message": "Bad request"}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        adapter._session = mock_session

        receipt = asyncio.run(adapter.send_message("+123", "Hi"))
        assert receipt.ok is False
        assert "Bad request" in (receipt.error or "")
