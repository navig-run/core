"""Tests for navig.tools.approval."""

from __future__ import annotations

import os

import pytest

from navig.tools.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    get_approval_gate,
    reset_approval_gate,
)

# ---------------------------------------------------------------------------
# ApprovalGate — safe/moderate always pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_tool_always_approved():
    gate = ApprovalGate()
    d = await gate.check("my_tool", "safe")
    assert d == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_moderate_tool_always_approved():
    gate = ApprovalGate()
    d = await gate.check("my_tool", "moderate")
    assert d == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_dangerous_tool_default_approved(monkeypatch):
    """Default single-operator backend logs a warning but approves."""
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    gate = ApprovalGate()
    d = await gate.check("rm_all", "dangerous")
    assert d == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_bypass_env_var(monkeypatch):
    monkeypatch.setenv("NAVIG_ALLOW_ALL_COMMANDS", "1")

    # Even if we set a denying backend, env bypass wins
    async def always_deny(req):
        return ApprovalDecision.DENIED

    gate = ApprovalGate(backend=always_deny)
    d = await gate.check("rm_all", "dangerous")
    assert d == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_custom_backend_deny(monkeypatch):
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)

    async def deny_all(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    gate = ApprovalGate(backend=deny_all)
    d = await gate.check("rm_all", "dangerous")
    assert d == ApprovalDecision.DENIED


@pytest.mark.asyncio
async def test_backend_raises_returns_denied(monkeypatch):
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)

    async def exploding(req):
        raise RuntimeError("network failure")

    gate = ApprovalGate(backend=exploding)
    d = await gate.check("rm_all", "dangerous")
    assert d == ApprovalDecision.DENIED


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestGetApprovalGate:
    def setup_method(self):
        reset_approval_gate()

    def teardown_method(self):
        reset_approval_gate()

    def test_singleton(self):
        g1 = get_approval_gate()
        g2 = get_approval_gate()
        assert g1 is g2

    def test_backend_replaceable(self):
        gate = get_approval_gate()

        async def my_backend(req):
            return ApprovalDecision.APPROVED

        gate.backend = my_backend
        assert gate.backend is my_backend
