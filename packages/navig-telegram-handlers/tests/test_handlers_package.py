"""
tests/test_handlers_package.py - Unit tests for navig-telegram-handlers pack.

Covers:
- telegram/formatters.py: format_checkdomain() with all status variants
- telegram/menus.py: build_checkdomain_menu() graceful no-op when telegram absent
- handler.py: on_load / on_unload / on_event lifecycle contracts
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path munging so imports resolve from the package root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
TELEGRAM_DIR = ROOT / "telegram"

for _p in (str(ROOT), str(TELEGRAM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from formatters import FORMATTERS, format_checkdomain  # noqa: E402
from menus import MENUS, build_checkdomain_menu  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal handler dataclasses (mirror handler.py without importing it through
# the pack lifecycle which touches optional deps)
# ---------------------------------------------------------------------------
@dataclass
class _Ctx:
    pack_id: str = "navig-telegram-handlers"
    version: str = "0.1.0"
    store_path: Path = ROOT
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Evt:
    name: str
    payload: dict[str, Any]
    source: str = "test"


# ===========================================================================
# format_checkdomain
# ===========================================================================

class TestFormatCheckdomain:
    def test_available(self):
        result = {"status": "available", "domain": "example.com", "details": ""}
        text = format_checkdomain(result)
        assert "✅" in text
        assert "example.com" in text
        assert "available" in text.lower()

    def test_taken(self):
        result = {"status": "taken", "domain": "google.com", "details": ""}
        text = format_checkdomain(result)
        assert "❌" in text
        assert "google.com" in text
        assert "registered" in text.lower()

    def test_error_status(self):
        result = {"status": "error", "domain": "bad.xyz", "details": "timeout"}
        text = format_checkdomain(result)
        assert "⚠️" in text
        assert "bad.xyz" in text
        assert "timeout" in text

    def test_unknown_status_uses_question_mark(self):
        result = {"status": "pending", "domain": "foo.io", "details": ""}
        text = format_checkdomain(result)
        assert "❓" in text

    def test_empty_domain_fallback(self):
        result = {"status": "error", "domain": "", "details": ""}
        text = format_checkdomain(result)
        assert "Unknown domain" in text

    def test_details_appended(self):
        result = {"status": "available", "domain": "a.com", "details": "Extra info"}
        text = format_checkdomain(result)
        assert "Extra info" in text

    def test_registered_in_formatters_registry(self):
        assert "checkdomain" in FORMATTERS
        assert FORMATTERS["checkdomain"] is format_checkdomain


# ===========================================================================
# build_checkdomain_menu
# ===========================================================================

class TestBuildCheckdomainMenu:
    def test_returns_none_when_telegram_not_installed(self):
        """When python-telegram-bot is absent the menu builder returns None."""
        import menus as menus_mod
        original_ikm = menus_mod.InlineKeyboardMarkup
        try:
            menus_mod.InlineKeyboardMarkup = None
            menus_mod.InlineKeyboardButton = None
            result = {"status": "available", "domain": "example.com"}
            assert build_checkdomain_menu(result) is None
        finally:
            menus_mod.InlineKeyboardMarkup = original_ikm

    def test_registered_in_menus_registry(self):
        assert "checkdomain" in MENUS
        assert MENUS["checkdomain"] is build_checkdomain_menu

    def test_available_builds_registrar_buttons(self):
        """When telegram is available, available domains get registrar links."""
        fake_btn = MagicMock(side_effect=lambda text, url=None, **kw: (text, url))
        fake_ikm = MagicMock(side_effect=lambda rows: rows)

        import menus as menus_mod
        original_btn = menus_mod.InlineKeyboardButton
        original_ikm = menus_mod.InlineKeyboardMarkup
        try:
            menus_mod.InlineKeyboardButton = fake_btn
            menus_mod.InlineKeyboardMarkup = fake_ikm
            result = {"status": "available", "domain": "test.io"}
            built = build_checkdomain_menu(result)
            assert built is not None
            # fake_ikm was called → rows were passed
            assert fake_ikm.called
            # namecheap + porkbun buttons created
            calls = [str(c) for c in fake_btn.call_args_list]
            assert any("Namecheap" in c for c in calls)
            assert any("Porkbun" in c for c in calls)
        finally:
            menus_mod.InlineKeyboardButton = original_btn
            menus_mod.InlineKeyboardMarkup = original_ikm

    def test_taken_builds_whois_button(self):
        fake_btn = MagicMock(side_effect=lambda text, url=None, **kw: (text, url))
        fake_ikm = MagicMock(side_effect=lambda rows: rows)

        import menus as menus_mod
        original_btn = menus_mod.InlineKeyboardButton
        original_ikm = menus_mod.InlineKeyboardMarkup
        try:
            menus_mod.InlineKeyboardButton = fake_btn
            menus_mod.InlineKeyboardMarkup = fake_ikm
            result = {"status": "taken", "domain": "google.com"}
            built = build_checkdomain_menu(result)
            assert built is not None
            calls = [str(c) for c in fake_btn.call_args_list]
            assert any("WHOIS" in c for c in calls)
        finally:
            menus_mod.InlineKeyboardButton = original_btn
            menus_mod.InlineKeyboardMarkup = original_ikm

    def test_error_builds_retry_button(self):
        fake_btn = MagicMock(side_effect=lambda text, callback_data=None, **kw: (text, callback_data))
        fake_ikm = MagicMock(side_effect=lambda rows: rows)

        import menus as menus_mod
        original_btn = menus_mod.InlineKeyboardButton
        original_ikm = menus_mod.InlineKeyboardMarkup
        try:
            menus_mod.InlineKeyboardButton = fake_btn
            menus_mod.InlineKeyboardMarkup = fake_ikm
            result = {"status": "error", "domain": "fail.io"}
            built = build_checkdomain_menu(result)
            assert built is not None
            calls = [str(c) for c in fake_btn.call_args_list]
            assert any("Retry" in c for c in calls)
        finally:
            menus_mod.InlineKeyboardButton = original_btn
            menus_mod.InlineKeyboardMarkup = original_ikm


# ===========================================================================
# Handler lifecycle
# ===========================================================================

class TestHandlerLifecycle:
    def test_on_event_returns_none(self):
        """on_event is a no-op UX pack; must return None for any event."""
        sys.path.insert(0, str(ROOT))
        from handler import PluginContext, PluginEvent, on_event  # noqa: PLC0415

        ctx = PluginContext(pack_id="x", version="0", store_path=ROOT)
        evt = PluginEvent(name="message", payload={"text": "hi"}, source="tg")
        assert on_event(evt, ctx) is None

    def test_on_load_no_raise_when_registry_absent(self):
        """on_load must not raise even when navig-telegram is not installed."""
        sys.path.insert(0, str(ROOT))
        from handler import PluginContext, on_load  # noqa: PLC0415

        ctx = PluginContext(pack_id="x", version="0", store_path=ROOT)
        on_load(ctx)  # must not raise

    def test_on_unload_no_raise_when_registry_absent(self):
        """on_unload must not raise."""
        sys.path.insert(0, str(ROOT))
        from handler import PluginContext, on_unload  # noqa: PLC0415

        ctx = PluginContext(pack_id="x", version="0", store_path=ROOT)
        on_unload(ctx)  # must not raise

    def test_on_load_registers_when_registry_available(self):
        """When navig_telegram.handler_registry is importable, formatters are registered."""
        sys.path.insert(0, str(ROOT))
        from handler import PluginContext, on_load  # noqa: PLC0415

        registry_mock = MagicMock()
        registry_mock.register_formatter = MagicMock()
        registry_mock.register_menu = MagicMock()

        fake_navig_telegram = MagicMock()
        fake_navig_telegram.handler_registry = registry_mock

        with patch.dict("sys.modules", {"navig_telegram": fake_navig_telegram, "navig_telegram.handler_registry": registry_mock}):
            ctx = PluginContext(pack_id="x", version="0", store_path=ROOT)
            on_load(ctx)
            # At minimum, register_formatter must have been called once
            assert registry_mock.register_formatter.called
