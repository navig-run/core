"""An approval request must actually reach the operator, with buttons that resolve it.

Measured on the operator's install: 55 `approval_expired` incidents, every one
`channel="mission"`, `user_id="system"`, all auto-DENIED on a 120 s timeout. Nothing
unsafe ran (dangerous commands hard-deny regardless of policy) — but autonomous
remediation could never run, because nobody was ever asked.

`TelegramApprovalHandler` existed the whole time and was unreachable FOUR ways:

  1. never instantiated anywhere in the tree;
  2. early-returned unless `request.channel == "telegram"` — but the requests that
     need a human come from the MISSION channel;
  3. imported `telegram` (python-telegram-bot), which is not a dependency here, so
     the import raised into a broad `except` that logged and moved on;
  4. sent `reply_markup=<PTB object>` while this channel takes
     `keyboard: list[list[dict]]`.

The RESPONSE half was already wired — `create_telegram_channel` passes
`on_approval_response`, and the keyboard dispatcher routes a tap into
`manager.respond()`. But the gateway's own `_init_channels` built the channel
WITHOUT that argument, so on the live channel a tap answered "Approval system
unavailable". Two construction sites, one wired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navig.approval.handlers import TelegramApprovalHandler


class _Level:
    def __init__(self, value: str) -> None:
        self.value = value


@dataclass
class _Req:
    id: str = "req-123"
    command: str = "Remediate health issues"
    channel: str = "mission"
    user_id: str = "system"          # exactly what the live incidents carry
    level: Any = field(default_factory=lambda: _Level("confirm"))
    expires_at: Any = None


class _Manager:
    def __init__(self) -> None:
        self.registered = []

    def on_request(self, cb) -> None:
        self.registered.append(cb)


class _Builder:
    """Stands in for the channel's ResponseKeyboardBuilder."""

    def __init__(self) -> None:
        self.made = []

    def _make_button(self, text, action, msg_hash, extra=None, **kw):
        self.made.append((action, extra))
        return {"text": text, "callback_data": f"{action}:{msg_hash}"}


class _Channel:
    """The native channel: `keyboard=`, not PTB's `reply_markup=`."""

    def __init__(self) -> None:
        self._kb_builder = _Builder()
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None, keyboard=None, **kw):
        self.sent.append(
            {"chat_id": chat_id, "text": text, "keyboard": keyboard, "parse_mode": parse_mode}
        )
        return {"message_id": 1}


def test_constructing_the_handler_registers_it() -> None:
    """Building it IS the wiring — the constructor subscribes to the manager."""
    m = _Manager()
    TelegramApprovalHandler(m, bot=_Channel(), owner_chat_id=159901607)
    assert m.registered, "the handler did not subscribe to approval requests"


async def test_a_mission_approval_is_sent_with_resolvable_buttons() -> None:
    """THE regression: channel='mission', user_id='system' — the real shape."""
    ch = _Channel()
    h = TelegramApprovalHandler(_Manager(), bot=ch, owner_chat_id=159901607)

    await h.on_approval_request(_Req())

    assert ch.sent, (
        "a mission approval sent nothing — this is the state in which 55 requests "
        "expired unanswered"
    )
    msg = ch.sent[0]
    assert msg["chat_id"] == 159901607, "user_id='system' must fall back to the owner"
    assert "Remediate health issues" in msg["text"]
    kb = msg["keyboard"]
    assert kb and len(kb[0]) == 2, "an approval with no buttons cannot be answered"
    actions = [a for a, _ in ch._kb_builder.made]
    assert actions == ["approve", "cancel"], (
        "the buttons must use the actions the EXISTING dispatcher understands "
        f"(approve/cancel), got {actions}"
    )
    for _, extra in ch._kb_builder.made:
        assert extra and extra.get("request_id") == "req-123", (
            "the request id must ride in `extra` — the dispatcher reads it from "
            "there, and a hand-rolled callback_data arrives with no entry at all"
        )


async def test_a_cli_approval_is_not_pushed_to_telegram() -> None:
    """CLI answers in the terminal it was typed in; pushing it too is noise."""
    ch = _Channel()
    h = TelegramApprovalHandler(_Manager(), bot=ch, owner_chat_id=1)
    await h.on_approval_request(_Req(channel="cli"))
    assert ch.sent == []


async def test_a_numeric_user_id_wins_over_the_owner() -> None:
    ch = _Channel()
    h = TelegramApprovalHandler(_Manager(), bot=ch, owner_chat_id=999)
    await h.on_approval_request(_Req(user_id="4242"))
    assert ch.sent[0]["chat_id"] == 4242


async def test_no_destination_sends_nothing_rather_than_crashing() -> None:
    """int('system') raises — the old code would have died here."""
    ch = _Channel()
    h = TelegramApprovalHandler(_Manager(), bot=ch, owner_chat_id=None)
    await h.on_approval_request(_Req())
    assert ch.sent == []


def test_the_handler_does_not_depend_on_python_telegram_bot() -> None:
    """`telegram` is not a dependency here; importing it swallowed the whole send.

    Checked on the AST, not the text: the docstrings in this file and in the handler
    both MENTION `reply_markup` and `from telegram import` while describing the old
    bug, so a substring scan fails on correct code. (It did — that is why this reads
    the tree.)
    """
    import ast
    import inspect

    from navig.approval import handlers

    tree = ast.parse(inspect.getsource(handlers.TelegramApprovalHandler))

    imported = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    } | {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names
    }
    assert not any(m == "telegram" or m.startswith("telegram.") for m in imported), (
        "python-telegram-bot is not installed; that import raised into the broad "
        f"except and silently disabled every approval prompt. imports={sorted(imported)}"
    )

    kwargs = {
        kw.arg
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        for kw in n.keywords
        if kw.arg
    }
    assert "reply_markup" not in kwargs, (
        "this channel takes keyboard=, not PTB's reply_markup="
    )
    assert "keyboard" in kwargs, (
        "anti-vacuity floor: no keyword named `keyboard` is passed anywhere, so the "
        "check above is comparing against a send that does not exist"
    )
