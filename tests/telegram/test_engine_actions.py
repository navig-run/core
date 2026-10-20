"""Engine control from Telegram — /restart, /doctor, /os.

Regression: ``/restart`` ran ``sudo systemctl restart navig-daemon`` — Linux-only, so on
the operator's Windows machine the pocket remote's one engine control did nothing. It
now goes through ``request_daemon_restart``, the SAME seam the deck route and NAVIG OS
use, so the three surfaces cannot drift apart again.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from navig.gateway.channels import telegram_commands as tc
from navig.gateway.channels.telegram_extensions import EXTENSIONS
from navig.gateway.routes import daemon as daemon_routes
from navig.telegram import engine_actions as ea


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text, parse_mode="HTML", keyboard=None, **_):
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return {"message_id": 100 + len(self.sent)}

    async def edit_message(self, chat_id, message_id, text, parse_mode="HTML", keyboard=None):
        self.edits.append({"message_id": message_id, "text": text, "keyboard": keyboard})
        return {"message_id": message_id}


# ── restart goes through the shared seam ────────────────────────────────────


def test_restart_handler_no_longer_shells_out_to_systemctl():
    src = inspect.getsource(tc.TelegramCommandsMixin._handle_restart)
    assert "systemctl" not in src and "sudo" not in src
    assert "engine_actions.restart_daemon" in src


async def test_restart_on_windows_hands_off_to_the_task(monkeypatch):
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(daemon_routes, "_run_restart_task", lambda: (True, "started"))

    async def never(*_a, **_k):
        raise AssertionError("the in-tree restarter must never run on Windows")

    monkeypatch.setattr(daemon_routes, "_run_service_action", never)
    monkeypatch.setattr("navig.daemon.restart_cause.record", lambda *a, **k: None)

    ch = FakeChannel()
    out = await ea.restart_daemon(ch, 1, 55, user_id=7)

    assert out["result_code"] == "restart_requested_via_task"
    assert ch.edits and ch.edits[-1]["message_id"] == 55
    assert ch.edits[-1]["text"] == ea._t("engine.restart.via_task")


async def test_restart_without_the_task_tells_the_remedy(monkeypatch):
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(daemon_routes, "_run_restart_task", lambda: (False, "not found"))
    monkeypatch.setattr("navig.daemon.restart_cause.record", lambda *a, **k: None)

    out = await daemon_routes.request_daemon_restart("test", actor="t")
    assert out["ok"] is False and out["http_status"] == 409
    assert "navig service install" in out["message"]
    assert "navig service install" in ea.restart_outcome_text(out)


async def test_restart_cause_is_recorded_with_the_actor(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(daemon_routes, "_run_restart_task", lambda: (True, "ok"))
    monkeypatch.setattr(
        "navig.daemon.restart_cause.record", lambda src, **d: seen.append((src, d))
    )
    await daemon_routes.request_daemon_restart("telegram /restart", actor="telegram:7")
    assert seen == [("telegram /restart", {"actor": "telegram:7"})]


# ── buttons ─────────────────────────────────────────────────────────────────


async def test_restart_button_asks_before_restarting(monkeypatch):
    called: list[int] = []

    async def fake_restart(*a, **k):
        called.append(1)
        return {"ok": True}

    monkeypatch.setattr(ea, "restart_daemon", fake_restart)
    ch = FakeChannel()

    await ea.handle_callback(ch, "eng:rst", 1, 10, 7)
    assert called == [], "a single tap must not restart"
    confirm = [b["callback_data"] for b in ch.sent[-1]["keyboard"][0]]
    assert confirm == ["eng:rst!", "eng:no"]

    await ea.handle_callback(ch, "eng:rst!", 1, 11, 7)
    assert called == [1]


async def test_cancel_does_not_restart(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("cancel must not restart")

    monkeypatch.setattr(ea, "restart_daemon", boom)
    ch = FakeChannel()
    await ea.handle_callback(ch, "eng:no", 1, 12, 7)
    assert ch.edits[-1]["text"] == ea._t("engine.restart.cancelled")


async def test_unknown_action_is_refused():
    assert await ea.handle_callback(FakeChannel(), "eng:zap", 1, 1, 1) == ea._t("engine.toast.unknown")


# ── rendering ───────────────────────────────────────────────────────────────


def test_doctor_shows_only_what_needs_attention():
    report = {
        "ok": False,
        "summary": {"passed": 40, "warnings": 1, "failed": 1},
        "version": "9.9.9",
        "sections": [
            {
                "name": "Core",
                "checks": [
                    {"label": "Config", "ok": True, "warn": False, "detail": "fine"},
                    {"label": "Gateway", "ok": False, "warn": False, "detail": "down <b>"},
                    {"label": "Vault", "ok": False, "warn": True, "detail": "locked"},
                ],
            }
        ],
    }
    text = ea.render_doctor(report)
    assert "Gateway" in text and "Vault" in text and "Config" not in text
    assert "&lt;b&gt;" in text, "check details are escaped before going out as HTML"
    assert "9.9.9" in text


def test_doctor_caps_the_issue_list():
    checks = [{"label": f"c{i}", "ok": False, "warn": True, "detail": ""} for i in range(20)]
    text = ea.render_doctor({"ok": False, "summary": {}, "sections": [{"name": "s", "checks": checks}]})
    assert "c7" in text and "c8" not in text


def test_heal_lists_the_manual_hints():
    payload = {
        "ok": False,
        "actions": [
            {"label": "Daemon", "executed": True, "healed": True},
            {"label": "Webhook", "executed": False, "hint": "navig service restart"},
        ],
        "after": {"warnings": 1, "failed": 0},
    }
    text = ea.render_heal(payload)
    assert "navig service restart" in text


def test_os_card_names_running_and_devices():
    text = ea.render_os({"running": True, "kind": "desktop", "devices": [{"name": "iPad"}]})
    assert "iPad" in text and "desktop" in text
    assert ea._t("engine.os.stopped") in ea.render_os({"running": False, "devices": []})


async def test_send_doctor_reports_a_failure_instead_of_going_quiet(monkeypatch):
    async def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(ea, "_collect_doctor", broken)
    ch = FakeChannel()
    await ea.send_doctor(ch, 1)
    assert "boom" in ch.edits[-1]["text"]


# ── wiring ──────────────────────────────────────────────────────────────────


def test_new_commands_are_registered_and_core():
    names = {e.command: e for e in tc._SLASH_REGISTRY}
    for cmd, handler in (("doctor", "_handle_doctor_cmd"), ("os", "_handle_os_cmd")):
        assert names[cmd].handler == handler
        assert callable(getattr(tc.TelegramCommandsMixin, handler))
    core = next(e for e in EXTENSIONS if e.id == "core")
    assert {"doctor", "os"} <= set(core.commands)
    assert "eng:" in core.callback_prefixes


def test_eng_callbacks_are_routed():
    src = Path(inspect.getfile(tc)).with_name("telegram_keyboards.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == "eng:"
    ]
    assert hits, "telegram_keyboards must route eng:* callbacks to engine_actions"


@pytest.mark.parametrize("lang", ["en", "fr", "ru"])
def test_every_engine_key_exists_in_every_locale(lang):
    import json

    src = Path(ea.__file__).read_text(encoding="utf-8")
    used = {
        n.args[0].value
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "_t"
        and n.args
        and isinstance(n.args[0], ast.Constant)
    }
    assert len(used) > 20, "scan floor: the key scan read the module"
    catalog = json.loads(
        (Path(ea.__file__).parent.parent / "locales" / f"{lang}.json").read_text(encoding="utf-8")
    )
    missing = sorted(k for k in used if k not in catalog)
    assert not missing, f"{lang}.json lacks {missing}"
