"""Regression: the health-check "key rejected" alert ships a working one-tap fix.

The alert used to end in ``Fix: navig mode set big_tasks --provider <p> --model <m>``,
with the placeholders left for the operator to fill in from a phone. Now:

* the alert carries a "🔁 Switch to a working model" button, only for findings a
  model swap actually fixes (auth / nokey / dead modes, never tiers or catalogs);
* the button re-points the mode at a model that answered a fresh probe, honours
  ``llm.fallback.never_from`` / ``never_to``, and reports how to undo it;
* the keyboard survives the notify router on BOTH Telegram paths (the live
  channel's ``send_alert`` and the off-gateway ``notify_operator``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from navig.heartbeat import alert_text
from navig.llm import mode_repair


def _issue(mode: str, status: str, spec: str = "anthropic:claude-opus-4-8") -> str:
    # The exact shape heartbeat.runner writes for a mode finding.
    return (
        f"[HIGH] LLM mode '{mode}' → {spec} is {status} "
        f"(auth failed (401 — key/token invalid or expired)). "
        f"Fix: navig mode set {mode} --provider <p> --model <m>"
    )


# ── which findings get a button ──────────────────────────────────────────────


def test_only_swap_fixable_mode_findings_get_a_button():
    issues = [
        _issue("big_tasks", "auth"),
        _issue("coding", "auth"),
        _issue("chat", "slow"),  # passing condition — a swap is not the fix
        "[HIGH] routing tier 'small' → groq:x is dead (gone). Fix: navig mode route set small",
        "[MEDIUM] disk 91% full",
    ]

    assert alert_text.repairable_modes(issues) == ["big_tasks", "coding"]
    kb = alert_text.repair_keyboard(issues)
    assert kb == [[{
        "text": "🔁 Switch these to working models",
        "callback_data": "hbfix:big_tasks,coding",
    }]]


def test_no_fixable_finding_means_no_button():
    assert alert_text.repair_keyboard(["[LOW] something else"]) is None


def test_callback_data_never_exceeds_telegrams_64_byte_cap():
    issues = [_issue(f"mode_number_{i}", "auth") for i in range(8)]
    data = alert_text.repair_keyboard(issues)[0][0]["callback_data"]
    assert len(data.encode()) <= 64


def test_handler_prefix_matches_the_button_prefix():
    import inspect

    from navig.gateway.channels import telegram_keyboards

    assert alert_text.REPAIR_CALLBACK_PREFIX == "hbfix:"
    assert 'cb_data.startswith("hbfix:")' in inspect.getsource(telegram_keyboards)


# ── the repair itself ────────────────────────────────────────────────────────


class _FakeRouter:
    def __init__(self, provider: str, model: str) -> None:
        self.cfg = SimpleNamespace(provider=provider, model=model)
        self.modes = SimpleNamespace(get_mode=lambda _m: self.cfg)
        self.updates: list[tuple[str, str]] = []

    def resolve_mode(self, m: str) -> str:
        return m

    def update_mode(self, _mode, provider=None, model=None, **_kw) -> bool:
        self.updates.append((provider, model))
        self.cfg.provider, self.cfg.model = provider, model
        return True

    def get_all_modes(self) -> dict:
        return {}


@pytest.fixture
def router(monkeypatch):
    r = _FakeRouter("anthropic", "claude-opus-4-8")
    monkeypatch.setattr("navig.llm.router.get_llm_router", lambda: r)
    monkeypatch.setattr("navig.llm.model_fallback.fallback_allowed", lambda *_a, **_k: True)
    return r


def test_repair_skips_a_dead_candidate_and_uses_the_first_live_one(router, monkeypatch):
    monkeypatch.setattr(
        mode_repair, "_candidates", lambda *_a: ["openai:gpt-dead", "openrouter:claude-opus"]
    )
    verdicts = {"gpt-dead": ("auth", "401"), "claude-opus": ("live", "")}

    out = mode_repair.repair_mode(
        "big_tasks", probe=lambda p, m, timeout: verdicts[m], persist=False
    )

    assert out.ok and out.current == "openrouter:claude-opus"
    assert out.previous == "anthropic:claude-opus-4-8"
    assert router.updates == [("openrouter", "claude-opus")]
    text = mode_repair.outcome_text([out])
    assert "navig mode set big_tasks --provider anthropic --model claude-opus-4-8" in text


def test_repair_writes_nothing_when_no_candidate_answers(router, monkeypatch):
    monkeypatch.setattr(mode_repair, "_candidates", lambda *_a: ["openai:gpt-x"])

    out = mode_repair.repair_mode(
        "big_tasks", probe=lambda *_a, **_k: ("unreachable", "down"), persist=False
    )

    assert not out.ok
    assert router.updates == []


def test_never_from_mode_is_left_alone(router, monkeypatch):
    monkeypatch.setattr("navig.llm.model_fallback.fallback_allowed", lambda *_a, **_k: False)
    monkeypatch.setattr(mode_repair, "_candidates", lambda *_a: ["openai:gpt-x"])

    out = mode_repair.repair_mode("big_tasks", probe=lambda *_a, **_k: ("live", ""), persist=False)

    assert not out.ok and "never_from" in out.reason
    assert router.updates == []


# ── the keyboard reaches Telegram ────────────────────────────────────────────


async def test_router_hands_the_keyboard_to_the_live_telegram_channel(monkeypatch):
    from navig.notify.router import NotificationRouter

    channel = SimpleNamespace(send_alert=AsyncMock(return_value=True))
    nm = SimpleNamespace(get_channel=lambda name: channel if name == "telegram" else None)
    monkeypatch.setattr("navig.gateway.notifications.get_notification_manager", lambda: nm)
    kb = [[{"text": "x", "callback_data": "hbfix:coding"}]]

    ok, _ = await NotificationRouter()._send_one(
        "telegram", "system_alert", "T", "B", "high", {}, keyboard=kb
    )

    assert ok
    assert channel.send_alert.await_args.kwargs["keyboard"] == kb


def test_notify_operator_sends_the_keyboard_as_reply_markup(monkeypatch):
    from navig.messaging import notify_operator as mod

    seen: dict = {}
    monkeypatch.setattr(
        "navig.telegram.api.telegram_send", lambda **kw: seen.update(kw) or 1
    )
    kb = [[{"text": "x", "callback_data": "hbfix:coding"}]]

    assert mod.notify_operator("hello", chat_id="42", keyboard=kb)
    assert seen["reply_markup"] == {"inline_keyboard": kb}


async def test_pressing_the_button_repairs_and_removes_it(monkeypatch):
    from navig.gateway.channels import telegram_keyboards as kb
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    channel._api_call = AsyncMock(return_value={"ok": True})
    channel.send_message = AsyncMock(return_value={"message_id": 2})
    channel.edit_message_reply_markup = AsyncMock(return_value={})
    asked: list = []

    def _fake_repair(modes):
        asked.append(modes)
        return [mode_repair.RepairOutcome("coding", True, "anthropic:x", "openrouter:y")]

    monkeypatch.setattr(mode_repair, "repair_modes", _fake_repair)

    await kb.CallbackHandler(channel).handle({
        "id": "cb1",
        "data": "hbfix:coding",
        "from": {"id": 5},
        "message": {"message_id": 9, "chat": {"id": 5, "type": "private"}},
    })

    assert asked == [["coding"]]
    channel.edit_message_reply_markup.assert_awaited_once_with(5, 9, None)
    reply = channel.send_message.await_args.args[1]
    assert "openrouter:y" in reply
