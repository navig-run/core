"""Tests for navig/messaging/adapters/whatsapp_cloud.py — WhatsAppCloudAdapter."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch


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


class TestWhatsAppMediaHonesty:
    """WhatsApp Cloud must NOT silently drop media and report success — it fetches media by
    public link and sends one media object per message, so a local (bytes/path) attachment or
    extra attachments are refused rather than sent as text-only with a success() receipt.

    The refuse guard returns before any HTTP call, so these tests need no network mock. Before
    the fix, `_whatsapp_payload` fell through to a text payload and send_message returned
    success()."""

    def _adapter(self):
        from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter
        return WhatsAppCloudAdapter({})

    async def test_bytes_attachment_is_refused_and_nothing_is_sent(self):
        adapter = self._adapter()
        # If the guard failed to fire, send_message would await _get_session() and POST a
        # (media-stripped) text message. Prove it never gets there.
        with patch.object(adapter, "_get_session", new=AsyncMock()) as mock_session:
            r = await adapter.send_message(
                "+15550001234", "hi", attachments=[{"data": b"\x89PNG", "kind": "photo"}]
            )
        assert r.ok is False
        assert "url" in (r.error or "").lower()
        mock_session.assert_not_awaited()  # no network — refused before the POST

    async def test_local_path_attachment_is_refused(self):
        adapter = self._adapter()
        with patch.object(adapter, "_get_session", new=AsyncMock()) as mock_session:
            r = await adapter.send_message(
                "+15550001234", "hi", attachments=[{"path": "/tmp/a.png", "kind": "photo"}]
            )
        assert r.ok is False
        assert "url" in (r.error or "").lower()
        mock_session.assert_not_awaited()

    async def test_multiple_url_attachments_are_refused_not_partially_sent(self):
        adapter = self._adapter()
        with patch.object(adapter, "_get_session", new=AsyncMock()) as mock_session:
            r = await adapter.send_message(
                "+15550001234",
                "hi",
                attachments=[{"url": "https://x/a.png"}, {"url": "https://x/b.png"}],
            )
        assert r.ok is False
        assert "one media" in (r.error or "").lower()
        mock_session.assert_not_awaited()  # not even the first is sent — refuse, don't partial-send
