"""Tests for TelegramApprovalBackend (FA7 — Telegram Approval).

Covers:
- Message formatting (emojis, risk levels, details truncation)
- Inline keyboard structure and callback data parsing
- Approve/deny callback resolution
- Timeout with risk-based auto-approve/auto-deny
- Backend integration with ApprovalGate
- Pending request tracking
- Edge cases (duplicate callbacks, unknown request IDs, send failure)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from navig.tools.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
)
from navig.tools.telegram_approval_backend import (
    AUTO_APPROVE_ON_TIMEOUT,
    CALLBACK_PREFIX,
    RISK_EMOJIS,
    ApprovalMessage,
    TelegramApprovalBackend,
    build_inline_keyboard,
    format_approval_message,
    parse_callback_data,
)

pytestmark = pytest.mark.integration

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _make_request(
    tool: str = "bash_exec",
    safety: str = "dangerous",
    reason: str = "",
    params: dict | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        tool_name=tool,
        safety_level=safety,
        parameters=params or {},
        reason=reason,
    )


def _make_backend(
    chat_id: str = "12345",
    timeout: int = 2,
    send_result: dict | None = None,
) -> TelegramApprovalBackend:
    """Create a backend with mocked send/edit functions."""
    result = send_result or {"result": {"message_id": 42}}

    async def mock_send(cid, text, markup):
        return result

    async def mock_edit(cid, mid, text):
        pass

    return TelegramApprovalBackend(
        chat_id=chat_id,
        timeout=timeout,
        send_fn=mock_send,
        edit_fn=mock_edit,
    )


# ─────────────────────────────────────────────────────────────
# TestMessageFormatting
# ─────────────────────────────────────────────────────────────


class TestMessageFormatting:
    """format_approval_message output."""

    def test_contains_tool_name(self):
        req = _make_request(tool="delete_file")
        text = format_approval_message(req, "req_001")
        assert "delete_file" in text

    def test_contains_risk_emoji(self):
        req = _make_request(safety="dangerous")
        text = format_approval_message(req, "req_001")
        assert RISK_EMOJIS["dangerous"] in text

    def test_contains_risk_level_upper(self):
        req = _make_request(safety="dangerous")
        text = format_approval_message(req, "req_001")
        assert "DANGEROUS" in text

    def test_contains_request_id(self):
        req = _make_request()
        text = format_approval_message(req, "abc123")
        assert "abc123" in text

    def test_contains_reason(self):
        req = _make_request(reason="Need to deploy hotfix")
        text = format_approval_message(req, "req_001")
        assert "Need to deploy hotfix" in text

    def test_contains_parameters(self):
        req = _make_request(params={"command": "rm -rf /tmp/old"})
        text = format_approval_message(req, "req_001")
        assert "rm -rf /tmp/old" in text

    def test_truncates_long_values(self):
        long_val = "x" * 500
        req = _make_request(params={"data": long_val})
        text = format_approval_message(req, "req_001")
        assert "…" in text
        assert long_val not in text

    def test_safe_emoji(self):
        req = _make_request(safety="safe")
        text = format_approval_message(req, "req_001")
        assert RISK_EMOJIS["safe"] in text

    def test_unknown_safety_falls_back(self):
        req = _make_request(safety="unknown_level")
        text = format_approval_message(req, "req_001")
        assert "🟡" in text  # fallback emoji

    def test_no_reason_omitted(self):
        req = _make_request(reason="")
        text = format_approval_message(req, "req_001")
        assert "Reason" not in text

    def test_no_params_omitted(self):
        req = _make_request(params={})
        text = format_approval_message(req, "req_001")
        assert "Details" not in text


# ─────────────────────────────────────────────────────────────
# TestInlineKeyboard
# ─────────────────────────────────────────────────────────────


class TestInlineKeyboard:
    """build_inline_keyboard structure."""

    def test_has_two_buttons(self):
        kb = build_inline_keyboard("req_001")
        buttons = kb["inline_keyboard"][0]
        assert len(buttons) == 2

    def test_approve_button(self):
        kb = build_inline_keyboard("req_001")
        approve = kb["inline_keyboard"][0][0]
        assert "Approve" in approve["text"]
        assert approve["callback_data"] == f"{CALLBACK_PREFIX}approve:req_001"

    def test_deny_button(self):
        kb = build_inline_keyboard("req_001")
        deny = kb["inline_keyboard"][0][1]
        assert "Deny" in deny["text"]
        assert deny["callback_data"] == f"{CALLBACK_PREFIX}deny:req_001"

    def test_request_id_in_callback_data(self):
        kb = build_inline_keyboard("abc_xyz")
        for btn in kb["inline_keyboard"][0]:
            assert "abc_xyz" in btn["callback_data"]


# ─────────────────────────────────────────────────────────────
# TestCallbackParsing
# ─────────────────────────────────────────────────────────────


class TestCallbackParsing:
    """parse_callback_data logic."""

    def test_approve_callback(self):
        result = parse_callback_data(f"{CALLBACK_PREFIX}approve:req_001")
        assert result == ("approve", "req_001")

    def test_deny_callback(self):
        result = parse_callback_data(f"{CALLBACK_PREFIX}deny:req_002")
        assert result == ("deny", "req_002")

    def test_invalid_prefix(self):
        assert parse_callback_data("other:approve:req_001") is None

    def test_empty_string(self):
        assert parse_callback_data("") is None

    def test_missing_action(self):
        assert parse_callback_data(f"{CALLBACK_PREFIX}req_001") is None

    def test_invalid_action(self):
        assert parse_callback_data(f"{CALLBACK_PREFIX}maybe:req_001") is None

    def test_complex_request_id(self):
        result = parse_callback_data(f"{CALLBACK_PREFIX}approve:abc-123-def")
        assert result == ("approve", "abc-123-def")


# ─────────────────────────────────────────────────────────────
# TestApproveCallback
# ─────────────────────────────────────────────────────────────


class TestApproveCallback:
    """Approval via callback resolves the future."""

    def test_approve_resolves_true(self):
        async def _run():
            backend = _make_backend(timeout=5)
            req = _make_request()

            # Start the approval in a task
            task = asyncio.create_task(backend(req))
            await asyncio.sleep(0.05)  # let send complete

            # Find the pending request
            assert backend.pending_count == 1
            request_id = list(backend._pending.keys())[0]

            # Simulate approve callback
            handled = backend.handle_callback(f"{CALLBACK_PREFIX}approve:{request_id}")
            assert handled is True

            decision = await task
            assert decision == ApprovalDecision.APPROVED

        asyncio.run(_run())

    def test_deny_resolves_denied(self):
        async def _run():
            backend = _make_backend(timeout=5)
            req = _make_request()

            task = asyncio.create_task(backend(req))
            await asyncio.sleep(0.05)

            request_id = list(backend._pending.keys())[0]
            backend.handle_callback(f"{CALLBACK_PREFIX}deny:{request_id}")

            decision = await task
            assert decision == ApprovalDecision.DENIED

        asyncio.run(_run())

    def test_callback_clears_pending(self):
        async def _run():
            backend = _make_backend(timeout=5)
            req = _make_request()

            task = asyncio.create_task(backend(req))
            await asyncio.sleep(0.05)

            request_id = list(backend._pending.keys())[0]
            backend.handle_callback(f"{CALLBACK_PREFIX}approve:{request_id}")
            await task

            assert backend.pending_count == 0

        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# TestTimeoutPolicy
# ─────────────────────────────────────────────────────────────


class TestTimeoutPolicy:
    """Timeout behavior based on risk level."""

    def test_timeout_auto_approves_safe(self):
        backend = _make_backend()
        assert backend._timeout_policy("safe") is True

    def test_timeout_auto_approves_moderate(self):
        backend = _make_backend()
        assert backend._timeout_policy("moderate") is True

    def test_timeout_auto_denies_dangerous(self):
        backend = _make_backend()
        assert backend._timeout_policy("dangerous") is False

    def test_timeout_auto_denies_critical(self):
        backend = _make_backend()
        assert backend._timeout_policy("critical") is False

    def test_timeout_dangerous_returns_timeout_decision(self):
        async def _run():
            backend = _make_backend(timeout=0)  # instant timeout
            req = _make_request(safety="dangerous")
            decision = await backend(req)
            assert decision == ApprovalDecision.TIMEOUT

        asyncio.run(_run())

    def test_timeout_safe_returns_approved(self):
        async def _run():
            backend = _make_backend(timeout=0)
            req = _make_request(safety="safe")
            decision = await backend(req)
            assert decision == ApprovalDecision.APPROVED

        asyncio.run(_run())

    def test_custom_auto_approve_levels(self):
        backend = TelegramApprovalBackend(
            chat_id="1",
            auto_approve_levels=frozenset({"safe"}),
            send_fn=AsyncMock(return_value={"result": {"message_id": 1}}),
            edit_fn=AsyncMock(),
        )
        assert backend._timeout_policy("safe") is True
        assert backend._timeout_policy("moderate") is False


# ─────────────────────────────────────────────────────────────
# TestSendFailure
# ─────────────────────────────────────────────────────────────


class TestSendFailure:
    """Behavior when the send function fails."""

    def test_send_failure_returns_denied(self):
        async def _run():
            async def failing_send(cid, text, markup):
                raise ConnectionError("network down")

            backend = TelegramApprovalBackend(
                chat_id="1",
                timeout=5,
                send_fn=failing_send,
            )
            req = _make_request()
            decision = await backend(req)
            assert decision == ApprovalDecision.DENIED

        asyncio.run(_run())

    def test_send_failure_clears_pending(self):
        async def _run():
            async def failing_send(cid, text, markup):
                raise RuntimeError("boom")

            backend = TelegramApprovalBackend(
                chat_id="1",
                send_fn=failing_send,
            )
            await backend(_make_request())
            assert backend.pending_count == 0

        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# TestHandleCallbackEdgeCases
# ─────────────────────────────────────────────────────────────


class TestHandleCallbackEdgeCases:
    """Edge cases for handle_callback."""

    def test_unknown_request_id(self):
        backend = _make_backend()
        assert backend.handle_callback(f"{CALLBACK_PREFIX}approve:unknown_id") is False

    def test_invalid_callback_data(self):
        backend = _make_backend()
        assert backend.handle_callback("garbage") is False

    def test_double_callback_ignored(self):
        async def _run():
            backend = _make_backend(timeout=5)
            req = _make_request()

            task = asyncio.create_task(backend(req))
            await asyncio.sleep(0.05)

            request_id = list(backend._pending.keys())[0]
            first = backend.handle_callback(f"{CALLBACK_PREFIX}approve:{request_id}")
            second = backend.handle_callback(f"{CALLBACK_PREFIX}deny:{request_id}")

            assert first is True
            assert second is False  # future already done

            decision = await task
            assert decision == ApprovalDecision.APPROVED  # first wins

        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# TestMessageIdExtraction
# ─────────────────────────────────────────────────────────────


class TestMessageIdExtraction:
    """Message ID is extracted from send result."""

    def test_extracts_from_result_wrapper(self):
        async def _run():
            backend = _make_backend(
                timeout=5,
                send_result={"ok": True, "result": {"message_id": 999}},
            )
            task = asyncio.create_task(backend(_make_request()))
            await asyncio.sleep(0.05)

            request_id = list(backend._pending.keys())[0]
            msg = backend.get_pending(request_id)
            assert msg is not None
            assert msg.message_id == 999

            backend.handle_callback(f"{CALLBACK_PREFIX}approve:{request_id}")
            await task

        asyncio.run(_run())

    def test_extracts_from_flat_dict(self):
        async def _run():
            backend = _make_backend(
                timeout=5,
                send_result={"message_id": 777},
            )
            task = asyncio.create_task(backend(_make_request()))
            await asyncio.sleep(0.05)

            request_id = list(backend._pending.keys())[0]
            msg = backend.get_pending(request_id)
            assert msg is not None
            assert msg.message_id == 777

            backend.handle_callback(f"{CALLBACK_PREFIX}approve:{request_id}")
            await task

        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# TestGateIntegration
# ─────────────────────────────────────────────────────────────


class TestGateIntegration:
    """Integration with ApprovalGate."""

    def test_gate_uses_telegram_backend(self):
        async def _run():
            backend = _make_backend(timeout=5)
            gate = ApprovalGate(backend=backend)

            # Start gate check in a task
            task = asyncio.create_task(gate.check("bash_exec", "dangerous", {"command": "ls"}))
            await asyncio.sleep(0.05)

            # Approve via callback
            request_id = list(backend._pending.keys())[0]
            backend.handle_callback(f"{CALLBACK_PREFIX}approve:{request_id}")

            decision = await task
            assert decision == ApprovalDecision.APPROVED

        asyncio.run(_run())

    def test_gate_timeout_with_dangerous(self):
        async def _run():
            backend = _make_backend(timeout=0)
            gate = ApprovalGate(backend=backend)

            decision = await gate.check("bash_exec", "dangerous")
            # Dangerous + timeout → TIMEOUT (auto-deny)
            assert decision == ApprovalDecision.TIMEOUT

        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# TestConstants
# ─────────────────────────────────────────────────────────────


class TestConstants:
    """Module-level constants."""

    def test_risk_emojis_cover_levels(self):
        for level in ("safe", "moderate", "dangerous", "critical"):
            assert level in RISK_EMOJIS

    def test_auto_approve_levels(self):
        assert "safe" in AUTO_APPROVE_ON_TIMEOUT
        assert "moderate" in AUTO_APPROVE_ON_TIMEOUT
        assert "dangerous" not in AUTO_APPROVE_ON_TIMEOUT

    def test_callback_prefix(self):
        assert CALLBACK_PREFIX == "approval:"


# ─────────────────────────────────────────────────────────────
# TestApprovalMessage
# ─────────────────────────────────────────────────────────────


class TestApprovalMessage:
    """ApprovalMessage dataclass."""

    def test_defaults(self):
        msg = ApprovalMessage(
            request_id="r1",
            request=_make_request(),
        )
        assert msg.message_id is None
        assert msg.resolved_at is None
        assert msg.resolved_by == ""
        assert msg.created_at > 0

    def test_chat_id_stored(self):
        msg = ApprovalMessage(
            request_id="r1",
            request=_make_request(),
            chat_id="99",
        )
        assert msg.chat_id == "99"
