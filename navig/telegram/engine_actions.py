"""Engine control from Telegram — ``/restart``, ``/doctor``, ``/os`` and their buttons.

Telegram is the pocket remote for the engine: when the operator is away from the
computer it must be able to see whether NAVIG is healthy, heal it, restart it, and
tell whether NAVIG OS is up. Every action here goes through the SAME core seams the
deck and NAVIG OS use, so the three surfaces can never disagree:

* restart → ``navig.gateway.routes.daemon.request_daemon_restart`` (on Windows the
  on-demand scheduled task — a restarter spawned from inside the daemon's tree dies
  mid-stop, measured; on POSIX ``navig service restart``)
* doctor  → ``navig.commands.doctor.collect_report`` (exactly ``navig doctor --json``)
* heal    → ``navig doctor --heal --json`` in a subprocess (heal never restarts a LIVE
  daemon — that remediation is report-only — so it is safe to run from in here)
* NAVIG OS → the OS server's discovery descriptor + the paired-device store

Every string the operator reads is an ``engine.*`` key in ``navig/locales``.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "eng:"

_MAX_ISSUES_SHOWN = 8


def _t(key: str, **fields: Any) -> str:
    """One localized ``engine.*`` string; the key itself if the catalog lacks it."""
    try:
        from navig.core import i18n

        return i18n.t(key, **fields)
    except Exception:  # noqa: BLE001 — a card must never fail on a locale read
        return key


# ── restart ──────────────────────────────────────────────────────────────────


def restart_outcome_text(out: dict[str, Any]) -> str:
    """One plain-text line for a ``request_daemon_restart`` result."""
    if out.get("ok"):
        if out.get("result_code") == "restart_requested_via_task":
            return _t("engine.restart.via_task")
        return _t("engine.restart.done")
    if out.get("http_status") == 409:
        return _t("engine.restart.needs_task")
    return _t("engine.restart.failed", reason=out.get("message") or "?")


async def restart_daemon(channel: Any, chat_id: int, message_id: int | None, user_id: int) -> dict:
    """Restart the engine and report the outcome in *message_id* (or a new message).

    On POSIX the call may not return — this process is part of what restarts — so
    the "now…" line is already on screen before it is made.
    """
    from navig.gateway.routes.daemon import request_daemon_restart

    out = await request_daemon_restart("telegram /restart", actor=f"telegram:{user_id}")
    text = restart_outcome_text(out)
    try:
        if message_id:
            await channel.edit_message(chat_id, message_id, text, parse_mode=None)
        else:
            await channel.send_message(chat_id, text, parse_mode=None)
    except Exception:  # noqa: BLE001 — the restart already happened; the note is best-effort
        logger.debug("restart outcome note failed", exc_info=True)
    return out


# ── doctor ───────────────────────────────────────────────────────────────────


def _doctor_keyboard() -> list[list[dict]]:
    return [
        [
            {"text": _t("engine.button.heal"), "callback_data": "eng:heal"},
            {"text": _t("engine.button.recheck"), "callback_data": "eng:doc"},
        ],
        [{"text": _t("engine.button.restart"), "callback_data": "eng:rst"}],
    ]


def render_doctor(report: dict[str, Any]) -> str:
    """HTML summary of a doctor report: the verdict, then only what needs attention."""
    summary = report.get("summary") or {}
    head = _t("engine.doctor.healthy") if report.get("ok") else _t("engine.doctor.attention")
    lines = [
        head,
        _t(
            "engine.doctor.counts",
            passed=int(summary.get("passed") or 0),
            warnings=int(summary.get("warnings") or 0),
            failed=int(summary.get("failed") or 0),
        ),
    ]
    issues: list[str] = []
    for section in report.get("sections") or []:
        for check in section.get("checks") or []:
            if check.get("ok"):
                continue
            icon = "⚠️" if check.get("warn") else "❌"
            label = html.escape(str(check.get("label") or section.get("name") or "?"))
            detail = html.escape(str(check.get("detail") or ""))[:140]
            issues.append(f"{icon} {label}" + (f" — {detail}" if detail else ""))
    if issues:
        lines.append("")
        lines.extend(issues[:_MAX_ISSUES_SHOWN])
        if len(issues) > _MAX_ISSUES_SHOWN:
            lines.append(_t("engine.doctor.more", count=len(issues) - _MAX_ISSUES_SHOWN))
    version = report.get("version")
    if version:
        lines.append(f"\n<i>navig {html.escape(str(version))}</i>")
    return "\n".join(lines)


async def _collect_doctor() -> dict[str, Any]:
    from navig.daemon.engine_ops import collect_doctor

    return await collect_doctor()


async def send_doctor(channel: Any, chat_id: int, message_id: int | None = None) -> None:
    """Run the health check and show it, with Heal / Re-check / Restart buttons."""
    checking = _t("engine.doctor.checking")
    if message_id is None:
        msg = await channel.send_message(chat_id, checking, parse_mode=None)
        message_id = (msg or {}).get("message_id")
    else:
        await channel.edit_message(chat_id, message_id, checking, parse_mode=None)
    try:
        text = render_doctor(await _collect_doctor())
    except asyncio.TimeoutError:
        text = _t("engine.doctor.timeout")
    except Exception as exc:  # noqa: BLE001 — say what failed rather than going quiet
        logger.warning("telegram /doctor failed: %s", exc)
        text = _t("engine.doctor.failed", reason=html.escape(str(exc))[:200])
    if message_id:
        await channel.edit_message(chat_id, message_id, text, parse_mode="HTML", keyboard=_doctor_keyboard())
    else:
        await channel.send_message(chat_id, text, parse_mode="HTML", keyboard=_doctor_keyboard())


def render_heal(payload: dict[str, Any]) -> str:
    actions = payload.get("actions") or []
    healed = [a for a in actions if a.get("healed")]
    tried = [a for a in actions if a.get("executed")]
    manual = [a for a in actions if not a.get("executed") and a.get("hint")]
    after = payload.get("after") or payload.get("before") or {}
    head = _t("engine.heal.healthy") if payload.get("ok") else _t("engine.heal.finished")
    lines = [
        head,
        _t(
            "engine.heal.counts",
            healed=len(healed),
            tried=len(tried),
            warnings=int(after.get("warnings") or 0),
            failed=int(after.get("failed") or 0),
        ),
    ]
    for a in manual[:_MAX_ISSUES_SHOWN]:
        lines.append(f"• {html.escape(str(a.get('label') or ''))}: <code>{html.escape(str(a.get('hint')))}</code>")
    return "\n".join(lines)


async def _run_heal() -> dict[str, Any]:
    from navig.daemon.engine_ops import run_heal

    return await run_heal()


async def heal(channel: Any, chat_id: int, message_id: int | None) -> None:
    note = _t("engine.heal.running")
    if message_id:
        await channel.edit_message(chat_id, message_id, note, parse_mode=None)
    else:
        msg = await channel.send_message(chat_id, note, parse_mode=None)
        message_id = (msg or {}).get("message_id")
    try:
        text = render_heal(await _run_heal())
    except asyncio.TimeoutError:
        text = _t("engine.heal.timeout")
    except (ValueError, OSError) as exc:
        text = _t("engine.heal.no_result", reason=html.escape(str(exc))[:200])
    kb = [[{"text": _t("engine.button.recheck"), "callback_data": "eng:doc"}]]
    if message_id:
        await channel.edit_message(chat_id, message_id, text, parse_mode="HTML", keyboard=kb)
    else:
        await channel.send_message(chat_id, text, parse_mode="HTML", keyboard=kb)


# ── NAVIG OS ─────────────────────────────────────────────────────────────────


def os_facts() -> dict[str, Any]:
    """Is NAVIG OS running on this computer, and which devices are paired."""
    facts: dict[str, Any] = {"running": None, "kind": "", "devices": []}
    try:
        from navig.cloud.os_discovery import read_descriptor

        desc = read_descriptor()
        facts["running"] = desc is not None
        facts["kind"] = (desc or {}).get("kind", "")
    except Exception:  # noqa: BLE001
        logger.debug("os descriptor unreadable", exc_info=True)
    try:
        from navig.store.os_devices import get_os_device_store

        facts["devices"] = get_os_device_store().list_devices()
    except Exception:  # noqa: BLE001
        logger.debug("os device store unreadable", exc_info=True)
    return facts


def render_os(facts: dict[str, Any]) -> str:
    running = facts.get("running")
    if running is True:
        kind = f" ({html.escape(str(facts.get('kind')))})" if facts.get("kind") else ""
        head = _t("engine.os.running", kind=kind)
    elif running is False:
        head = _t("engine.os.stopped")
    else:
        head = _t("engine.os.unknown")
    devices = facts.get("devices") or []
    lines = [head, _t("engine.os.devices", count=len(devices))]
    for d in devices[:_MAX_ISSUES_SHOWN]:
        name = html.escape(str(d.get("name") or d.get("label") or d.get("id") or "?"))
        lines.append(f"• {name}")
    return "\n".join(lines)


async def send_os(channel: Any, chat_id: int) -> None:
    facts = await asyncio.to_thread(os_facts)
    await channel.send_message(chat_id, render_os(facts), parse_mode="HTML")


# ── buttons ──────────────────────────────────────────────────────────────────


async def handle_callback(
    channel: Any, cb_data: str, chat_id: int, message_id: int | None, user_id: int
) -> str:
    """Route an ``eng:*`` button. Returns the toast text."""
    from navig.core.background import spawn

    action = cb_data[len(CALLBACK_PREFIX):]
    if action == "doc":
        spawn(send_doctor(channel, chat_id, message_id))
        return _t("engine.toast.checking")
    if action == "heal":
        spawn(heal(channel, chat_id, message_id))
        return _t("engine.toast.healing")
    if action == "rst":
        # A restart interrupts every in-flight chat and job: ask once.
        await channel.send_message(
            chat_id,
            _t("engine.restart.confirm"),
            parse_mode=None,
            keyboard=[[
                {"text": _t("engine.button.confirm_restart"), "callback_data": "eng:rst!"},
                {"text": _t("engine.button.cancel"), "callback_data": "eng:no"},
            ]],
        )
        return ""
    if action == "rst!":
        if message_id:
            await channel.edit_message(chat_id, message_id, _t("engine.restart.now"), parse_mode=None)
        await restart_daemon(channel, chat_id, message_id, user_id)
        return _t("engine.toast.restarting")
    if action == "no":
        if message_id:
            await channel.edit_message(chat_id, message_id, _t("engine.restart.cancelled"), parse_mode=None)
        return _t("engine.toast.cancelled")
    return _t("engine.toast.unknown")
