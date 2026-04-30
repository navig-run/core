"""
Tests for navig/messaging/adapters/sms.py

Strategy: keep hermetic — mock the Twilio/Vonage SDKs so no real network
calls are made. Tests cover protocol properties, config resolution,
receive_webhook parsing, and send_message paths.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from navig.messaging.adapter import DeliveryReceipt, DeliveryStatus, InboundEvent
from navig.messaging.adapters.sms import SmsAdapter


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Protocol properties
# ---------------------------------------------------------------------------


class TestSmsAdapterProperties:
    def test_name_is_sms(self):
        assert SmsAdapter().name == "sms"

    def test_capabilities_includes_text(self):
        assert "text" in SmsAdapter().capabilities

    def test_identity_mode_is_business(self):
        assert SmsAdapter().identity_mode == "business"

    def test_compliance_is_official(self):
        assert SmsAdapter().compliance == "official"

    def test_default_provider_is_twilio(self):
        adapter = SmsAdapter()
        assert adapter._provider == "twilio"

    def test_custom_provider_stored(self):
        adapter = SmsAdapter({"provider": "vonage"})
        assert adapter._provider == "vonage"


# ---------------------------------------------------------------------------
# _resolve_from_number
# ---------------------------------------------------------------------------


class TestResolveFromNumber:
    def test_twilio_from_number_resolved(self):
        cfg = {"provider": "twilio", "twilio": {"from_number": "+1000000000"}}
        adapter = SmsAdapter(cfg)
        assert adapter._from_number == "+1000000000"

    def test_vonage_from_number_resolved(self):
        cfg = {"provider": "vonage", "vonage": {"from_number": "+1999999999"}}
        adapter = SmsAdapter(cfg)
        assert adapter._from_number == "+1999999999"

    def test_missing_from_number_defaults_to_empty(self):
        adapter = SmsAdapter({"provider": "twilio", "twilio": {}})
        assert adapter._from_number == ""

    def test_no_config_defaults_to_empty(self):
        adapter = SmsAdapter()
        assert adapter._from_number == ""


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------


class TestResolveTarget:
    def test_sms_prefix_stripped(self):
        target = SmsAdapter().resolve_target("sms:+33612345678")
        assert target.adapter == "sms"
        assert target.address == "+33612345678"

    def test_bare_number_used_as_address(self):
        target = SmsAdapter().resolve_target("+33612345678")
        assert target.address == "+33612345678"

    def test_whitespace_stripped_from_address(self):
        target = SmsAdapter().resolve_target("sms: +33612345678 ")
        assert target.address == "+33612345678"


# ---------------------------------------------------------------------------
# receive_webhook — inbound parsing
# ---------------------------------------------------------------------------


class TestReceiveWebhook:
    def test_twilio_webhook_parses_from(self):
        adapter = SmsAdapter({"provider": "twilio"})
        payload = {"From": "+441234567890", "Body": "Hello world"}
        event: InboundEvent = _run(adapter.receive_webhook(payload))
        assert event.sender == "+441234567890"
        assert event.text == "Hello world"
        assert event.adapter == "sms"

    def test_twilio_webhook_remote_id_is_from_number(self):
        adapter = SmsAdapter({"provider": "twilio"})
        payload = {"From": "+441234567890", "Body": "test"}
        event = _run(adapter.receive_webhook(payload))
        assert event.remote_conversation_id == "+441234567890"

    def test_twilio_webhook_missing_body_defaults_empty(self):
        adapter = SmsAdapter({"provider": "twilio"})
        event = _run(adapter.receive_webhook({"From": "+441234567890"}))
        assert event.text == ""

    def test_vonage_webhook_parses_msisdn(self):
        adapter = SmsAdapter({"provider": "vonage"})
        payload = {"msisdn": "33612345678", "text": "Bonjour"}
        event = _run(adapter.receive_webhook(payload))
        assert event.sender == "33612345678"
        assert event.text == "Bonjour"

    def test_vonage_webhook_raw_stored(self):
        adapter = SmsAdapter({"provider": "vonage"})
        payload = {"msisdn": "123", "text": "test"}
        event = _run(adapter.receive_webhook(payload))
        assert event.raw == payload


# ---------------------------------------------------------------------------
# _get_client — import errors
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_twilio_missing_raises_import_error(self):
        adapter = SmsAdapter({"provider": "twilio", "twilio": {}})
        with patch.dict("sys.modules", {"twilio": None, "twilio.rest": None}):
            with pytest.raises(ImportError, match="twilio"):
                adapter._get_client()

    def test_vonage_missing_raises_import_error(self):
        adapter = SmsAdapter({"provider": "vonage", "vonage": {}})
        with patch.dict("sys.modules", {"vonage": None}):
            with pytest.raises(ImportError, match="vonage"):
                adapter._get_client()

    def test_unknown_provider_raises_value_error(self):
        adapter = SmsAdapter({"provider": "carrier_pigeon"})
        with pytest.raises(ValueError, match="Unknown"):
            adapter._get_client()


# ---------------------------------------------------------------------------
# send_message — mocked provider paths
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_twilio_success_returns_delivery_receipt(self):
        mock_msg = MagicMock()
        mock_msg.sid = "SM_test_sid_123"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        adapter = SmsAdapter({
            "provider": "twilio",
            "twilio": {"account_sid": "ACxx", "auth_token": "tok", "from_number": "+1"},
        })
        adapter._client = mock_client
        receipt = _run(adapter.send_message("+33612345678", "Hello"))
        assert receipt.ok is True
        assert receipt.message_id == "SM_test_sid_123"

    def test_vonage_success_returns_delivery_receipt(self):
        mock_client = MagicMock()
        mock_client.sms.send_message.return_value = {
            "messages": [{"status": "0", "message-id": "VON_abc"}]
        }
        adapter = SmsAdapter({
            "provider": "vonage",
            "vonage": {"api_key": "k", "api_secret": "s", "from_number": "+1"},
        })
        adapter._client = mock_client
        receipt = _run(adapter.send_message("+33612345678", "Hello"))
        assert receipt.ok is True
        assert receipt.message_id == "VON_abc"

    def test_vonage_error_status_returns_failure(self):
        mock_client = MagicMock()
        mock_client.sms.send_message.return_value = {
            "messages": [{"status": "2", "error-text": "Invalid number"}]
        }
        adapter = SmsAdapter({
            "provider": "vonage",
            "vonage": {"api_key": "k", "api_secret": "s", "from_number": "+1"},
        })
        adapter._client = mock_client
        receipt = _run(adapter.send_message("+33000000000", "Hi"))
        assert receipt.ok is False
        assert "Invalid number" in receipt.error

    def test_unknown_provider_returns_failure(self):
        adapter = SmsAdapter({"provider": "fax"})
        # Force skip _get_client by injecting a dummy client
        adapter._client = MagicMock()  # won't be used — provider check comes first
        # Patch _get_client to return None so the provider branch is reached
        adapter._client = None
        adapter._provider = "fax"
        # Must not raise — returns failure receipt
        with patch.object(adapter, "_get_client", return_value=MagicMock()):
            receipt = _run(adapter.send_message("+1", "test"))
        assert receipt.ok is False

    def test_exception_during_send_returns_failure(self):
        adapter = SmsAdapter({"provider": "twilio", "twilio": {}})
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("network error")
        adapter._client = mock_client
        receipt = _run(adapter.send_message("+33612345678", "Hello"))
        assert receipt.ok is False
        assert "network error" in receipt.error
