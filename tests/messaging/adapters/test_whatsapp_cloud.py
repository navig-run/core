"""Tests for navig/messaging/adapters/whatsapp_cloud.py — WhatsAppCloudAdapter."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestWhatsAppCloudAdapterProperties:
    def _make(self):
        from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter
        with patch.object(WhatsAppCloudAdapter, "__init__", return_value=None):
            adapter = WhatsAppCloudAdapter.__new__(WhatsAppCloudAdapter)
            adapter._token = "tok"
            adapter._phone_number_id = "111"
            return adapter

    def test_name_is_whatsapp(self):
        adapter = self._make()
        assert adapter.name == "whatsapp"

    def test_capabilities_include_text(self):
        adapter = self._make()
        assert "text" in adapter.capabilities

    def test_identity_mode_is_business(self):
        adapter = self._make()
        assert adapter.identity_mode == "business"

    def test_compliance_is_official(self):
        adapter = self._make()
        assert adapter.compliance == "official"


class TestWhatsAppResolveTarget:
    def _make(self):
        from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter
        with patch.object(WhatsAppCloudAdapter, "__init__", return_value=None):
            adapter = WhatsAppCloudAdapter.__new__(WhatsAppCloudAdapter)
            adapter._token = "tok"
            adapter._phone_number_id = "111"
            return adapter

    def _get_address(self, result):
        """Resolve to string whether result is a str or ResolvedTarget."""
        return result.address if hasattr(result, "address") else result

    def test_resolve_strips_prefix(self):
        adapter = self._make()
        result = adapter.resolve_target("whatsapp:+15550001234")
        assert self._get_address(result) == "+15550001234"

    def test_resolve_no_prefix_unchanged(self):
        adapter = self._make()
        result = adapter.resolve_target("+15550001234")
        assert self._get_address(result) == "+15550001234"

    def test_resolve_empty_string(self):
        adapter = self._make()
        result = adapter.resolve_target("whatsapp:")
        assert self._get_address(result) == ""


class TestWhatsAppSendMessage:
    def test_send_message_is_coroutine(self):
        import inspect
        from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter
        with patch.object(WhatsAppCloudAdapter, "__init__", return_value=None):
            adapter = WhatsAppCloudAdapter.__new__(WhatsAppCloudAdapter)
        assert inspect.iscoroutinefunction(adapter.send_message)

    def test_send_message_accepts_thread_id_and_text(self):
        import inspect
        from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter
        sig = inspect.signature(WhatsAppCloudAdapter.send_message)
        assert "thread_id" in sig.parameters
        assert "text" in sig.parameters
