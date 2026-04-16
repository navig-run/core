"""Tests for navig.gateway.approvals."""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.approvals import (
    APPROVAL_PREFIX,
    ApprovalState,
    ApprovalStore,
    PendingApproval,
)


# ─────────────────────────────────────────────────────────────
# Store.create
# ─────────────────────────────────────────────────────────────


def test_create_returns_pending():
    store = ApprovalStore()
    entry = store.create("DROP TABLE users", timeout_s=10)
    assert isinstance(entry, PendingApproval)
    assert entry.state is ApprovalState.PENDING
    assert entry.id in store.all_ids()


def test_create_increments_pending_count():
    store = ApprovalStore()
    store.create("action A", timeout_s=10)
    store.create("action B", timeout_s=10)
    assert store.pending_count() == 2


# ─────────────────────────────────────────────────────────────
# approve / reject / cancel
# ─────────────────────────────────────────────────────────────


def test_approve_resolves():
    store = ApprovalStore()
    entry = store.create("restart nginx", timeout_s=10)
    result = store.approve(entry.id)
    assert result is True
    assert entry.state is ApprovalState.APPROVED
    assert entry.is_terminal()


def test_reject_resolves():
    store = ApprovalStore()
    entry = store.create("rm -rf /tmp/app", timeout_s=10)
    result = store.reject(entry.id)
    assert result is True
    assert entry.state is ApprovalState.REJECTED


def test_cancel_resolves():
    store = ApprovalStore()
    entry = store.create("dangerous op", timeout_s=10)
    result = store.cancel(entry.id)
    assert result is True
    assert entry.state is ApprovalState.CANCELLED


def test_double_approve_idempotent():
    store = ApprovalStore()
    entry = store.create("op", timeout_s=10)
    store.approve(entry.id)
    result = store.approve(entry.id)  # second call — already terminal
    assert result is False
    assert entry.state is ApprovalState.APPROVED  # unchanged


def test_unknown_id_returns_false():
    store = ApprovalStore()
    assert store.approve("nonexistent-id") is False
    assert store.reject("nonexistent-id") is False
    assert store.cancel("nonexistent-id") is False


# ─────────────────────────────────────────────────────────────
# wait()
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_resolves_on_approve():
    store = ApprovalStore()
    entry = store.create("safe op", timeout_s=5)

    async def _approve_after():
        await asyncio.sleep(0.05)
        store.approve(entry.id)

    asyncio.create_task(_approve_after())
    state = await store.wait(entry.id)
    assert state is ApprovalState.APPROVED


@pytest.mark.asyncio
async def test_wait_resolves_on_reject():
    store = ApprovalStore()
    entry = store.create("bad op", timeout_s=5)

    async def _reject_after():
        await asyncio.sleep(0.05)
        store.reject(entry.id)

    asyncio.create_task(_reject_after())
    state = await store.wait(entry.id)
    assert state is ApprovalState.REJECTED


@pytest.mark.asyncio
async def test_wait_times_out():
    store = ApprovalStore()
    entry = store.create("slow op", timeout_s=1)
    # Nobody approves — wait for the 1-second timeout
    state = await store.wait(entry.id)
    assert state is ApprovalState.TIMED_OUT


@pytest.mark.asyncio
async def test_wait_unknown_id_returns_cancelled():
    store = ApprovalStore()
    state = await store.wait("no-such-id")
    assert state is ApprovalState.CANCELLED


# ─────────────────────────────────────────────────────────────
# Callback helpers
# ─────────────────────────────────────────────────────────────


def test_encode_callback_ok():
    data = ApprovalStore.encode_callback("ok", "abc-123")
    assert data == f"{APPROVAL_PREFIX}ok_abc-123"


def test_encode_callback_no():
    data = ApprovalStore.encode_callback("no", "abc-123")
    assert data == f"{APPROVAL_PREFIX}no_abc-123"


def test_decode_callback_ok():
    data = f"{APPROVAL_PREFIX}ok_abc-123"
    result = ApprovalStore.decode_callback(data)
    assert result == ("ok", "abc-123")


def test_decode_callback_no():
    data = f"{APPROVAL_PREFIX}no_abc-123"
    result = ApprovalStore.decode_callback(data)
    assert result == ("no", "abc-123")


def test_decode_callback_unrelated_returns_none():
    assert ApprovalStore.decode_callback("some_other_callback") is None
    assert ApprovalStore.decode_callback("") is None


def test_handle_callback_approve():
    store = ApprovalStore()
    entry = store.create("op", timeout_s=10)
    data = ApprovalStore.encode_callback("ok", entry.id)
    handled = store.handle_callback(data)
    assert handled is True
    assert entry.state is ApprovalState.APPROVED


def test_handle_callback_reject():
    store = ApprovalStore()
    entry = store.create("op", timeout_s=10)
    data = ApprovalStore.encode_callback("no", entry.id)
    handled = store.handle_callback(data)
    assert handled is True
    assert entry.state is ApprovalState.REJECTED


def test_handle_callback_unrelated_returns_false():
    store = ApprovalStore()
    handled = store.handle_callback("unrelated_data")
    assert handled is False


# ─────────────────────────────────────────────────────────────
# build_message
# ─────────────────────────────────────────────────────────────


def test_build_message_structure():
    store = ApprovalStore()
    entry = store.create("DROP TABLE sessions", timeout_s=30)
    msg = store.build_message(entry)
    assert "text" in msg
    assert "reply_markup" in msg
    assert "DROP TABLE sessions" in msg["text"]
    keyboard = msg["reply_markup"]["inline_keyboard"]
    assert len(keyboard) == 1
    buttons = keyboard[0]
    assert len(buttons) == 2
    ok_btn, no_btn = buttons
    assert "ok" in ok_btn["callback_data"]
    assert "no" in no_btn["callback_data"]
    assert entry.id in ok_btn["callback_data"]
    assert entry.id in no_btn["callback_data"]
