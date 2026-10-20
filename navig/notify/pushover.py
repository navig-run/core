"""Pushover — the channel for alerts you must never miss.

Telegram cannot ring through a phone on silent, and it never repeats. Pushover
can: an **emergency** message (priority 2) uses iOS Critical Alerts / Android's
Do-Not-Disturb override, and re-alerts every ``retry`` seconds until you tap
*Acknowledge* (or ``expire`` runs out). That makes it the right channel for a
handful of types — a security alert, the sales check failing — and the wrong one
for everything else: an alarm that fires often stops being an alarm.

Credentials live in the vault, never in config or notify.db:

* ``pushover_app_token`` — the API token of the application you create at
  https://pushover.net/apps/build (one per sender; "NAVIG").
* ``pushover_user_key`` — your User Key, on the pushover.net dashboard.

Settings (``notify_settings``):

* ``pushover_emergency`` — which notification priorities become emergency
  (repeat-until-acknowledged): ``high`` (default: high + critical),
  ``critical`` (critical only) or ``never``. Below that, ``high`` → priority 1
  (skips Pushover's own quiet hours), ``normal`` → 0, ``low`` → -1.
* ``pushover_sound`` — a Pushover sound name (``siren``, ``persistent``,
  ``cashregister``…, or one you uploaded); blank = your device default.
* ``pushover_escalate_min`` — minutes an emergency may stay unacknowledged before
  it is escalated to SMS (default 15; ``0`` = never). Escalation needs an SMS
  target (Notifications → Channels → SMS).

Escalation: every emergency is recorded in ``notify_pushover_pending`` with its
receipt. The ``pushover_escalation`` monitor (default ON, idle when nothing is
pending) asks Pushover whether each due one was acknowledged; if not, it sends
the alert by SMS. Recorded in the DB, not in memory, so a daemon restart in
between does not lose the escalation.

API: https://pushover.net/api — messages, user validation and receipts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("navig.notify.pushover")

API = "https://api.pushover.net/1"
TOKEN_LABEL = "pushover_app_token"
USER_LABEL = "pushover_user_key"

#: Emergency re-alert interval and lifetime (Pushover: retry >= 30 s, expire <= 3 h).
RETRY_SEC = 60
EXPIRE_SEC = 3600

EMERGENCY_LEVELS = ("high", "critical", "never")
_TIMEOUT = 15
#: Pushover limits: title 250 chars, message 1024 chars.
_TITLE_MAX = 250
_MESSAGE_MAX = 1024


# ── credentials ──────────────────────────────────────────────────────────────


def _vault():
    from navig.vault.core import get_vault

    return get_vault()


def _read(label: str) -> str:
    try:
        from navig.vault.core import reveal_secret

        v = _vault()
        return (reveal_secret(v, label) or "").strip() if v is not None else ""
    except Exception:  # noqa: BLE001 — vault locked/absent reads as "not configured"
        logger.debug("pushover: vault read %s failed", label, exc_info=True)
        return ""


def credentials() -> tuple[str, str]:
    """``(app_token, user_key)`` — either may be ``""`` when not configured."""
    return _read(TOKEN_LABEL), _read(USER_LABEL)


def is_configured() -> bool:
    token, user = credentials()
    return bool(token and user)


def save_credentials(app_token: str, user_key: str) -> None:
    v = _vault()
    if v is None:
        raise RuntimeError("the vault is not available — run `navig vault init` first")
    v.put(TOKEN_LABEL, json.dumps({"value": app_token.strip()}).encode())
    v.put(USER_LABEL, json.dumps({"value": user_key.strip()}).encode())


# ── settings ─────────────────────────────────────────────────────────────────


def emergency_level() -> str:
    from navig.notify import prefs

    level = (prefs.get_raw("pushover_emergency", "high") or "high").strip().lower()
    return level if level in EMERGENCY_LEVELS else "high"


def sound() -> str:
    from navig.notify import prefs

    return (prefs.get_raw("pushover_sound", "") or "").strip()


def pushover_priority(priority: str, level: str | None = None) -> int:
    """NAVIG priority → Pushover priority (-1 … 2) under the emergency *level*."""
    level = level or emergency_level()
    if priority == "critical" and level in ("high", "critical"):
        return 2
    if priority == "high" and level == "high":
        return 2
    return {"critical": 1, "high": 1, "normal": 0, "low": -1}.get(priority, 0)


# ── API ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    ok: bool
    detail: str
    receipt: str = ""
    devices: tuple[str, ...] = ()


def _post(path: str, data: dict[str, Any]) -> dict[str, Any]:
    import requests

    resp = requests.post(f"{API}/{path}", data=data, timeout=_TIMEOUT)
    try:
        body = resp.json()
    except ValueError:
        return {"status": 0, "errors": [f"HTTP {resp.status_code}"]}
    return body if isinstance(body, dict) else {"status": 0, "errors": ["bad response"]}


def _errors(body: dict[str, Any]) -> str:
    errs = body.get("errors") or []
    return "; ".join(str(e) for e in errs) or "Pushover refused the request"


def validate(app_token: str, user_key: str) -> Outcome:
    """Check a token + user key pair with Pushover. Never raises."""
    if not app_token.strip() or not user_key.strip():
        return Outcome(False, "both the app token and the user key are needed")
    try:
        body = _post("users/validate.json", {"token": app_token.strip(), "user": user_key.strip()})
    except Exception as exc:  # noqa: BLE001
        return Outcome(False, f"couldn't reach Pushover: {exc}")
    if body.get("status") == 1:
        devices = tuple(str(d) for d in body.get("devices") or [])
        return Outcome(True, f"valid · {len(devices)} device(s)", devices=devices)
    errors = _errors(body)
    if "no active devices" in errors.lower():
        # The keys ARE right — the phone app just isn't logged in yet. Refusing would make
        # the operator re-enter correct keys later; save them and say what's missing.
        return Outcome(True, "keys valid — but no device yet: log in to the Pushover app on your phone")
    return Outcome(False, errors)


def send(
    title: str,
    message: str,
    *,
    priority: int = 0,
    sound_name: str = "",
    url: str = "",
) -> Outcome:
    """One Pushover message with the stored credentials. Never raises."""
    token, user = credentials()
    if not (token and user):
        return Outcome(False, "pushover not configured — run `navig notify pushover setup`")
    data: dict[str, Any] = {
        "token": token,
        "user": user,
        "title": (title or "NAVIG")[:_TITLE_MAX],
        "message": (message or title or "NAVIG")[:_MESSAGE_MAX],
        "priority": max(-2, min(2, int(priority))),
    }
    if data["priority"] == 2:
        data["retry"] = RETRY_SEC
        data["expire"] = EXPIRE_SEC
    if sound_name:
        data["sound"] = sound_name
    if url:
        data["url"] = url
    try:
        body = _post("messages.json", data)
    except Exception as exc:  # noqa: BLE001
        return Outcome(False, f"couldn't reach Pushover: {exc}")
    if body.get("status") == 1:
        receipt = str(body.get("receipt") or "")
        detail = "sent — repeats until acknowledged" if receipt else "sent"
        return Outcome(True, detail, receipt=receipt)
    return Outcome(False, _errors(body))


def send_notification(type_key: str, title: str, body: str, priority: str) -> tuple[bool, str]:
    """The router's entry point: a notification → one Pushover message."""
    from navig.notify.types import emoji_for_type

    out = send(
        f"{emoji_for_type(type_key)} {title}".strip(),
        body or title,
        priority=pushover_priority(priority),
        sound_name=sound(),
    )
    if not out.ok:
        logger.warning("pushover: %s delivery failed: %s", type_key, out.detail)
    elif out.receipt:
        try:
            record_pending(out.receipt, type_key, title, body)
        except Exception:  # noqa: BLE001 — the alert went out; escalation is the backstop
            logger.warning("pushover: could not record %s for escalation", out.receipt, exc_info=True)
    return out.ok, out.detail


# ── escalation ───────────────────────────────────────────────────────────────

_PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_pushover_pending (
  receipt TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  sent_at REAL NOT NULL,
  escalate_at REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  call_at REAL
)
"""

ESCALATE_DEFAULT_MIN = 15


def _db():
    from navig.notify import store

    store.init_db()
    c = store.conn()
    c.execute(_PENDING_SCHEMA)
    # The call stage was added after the table existed on real installs: add the
    # column once, or every SMS escalation would fail against a schema without it.
    cols = {r[1] for r in c.execute("PRAGMA table_info(notify_pushover_pending)").fetchall()}
    if "call_at" not in cols:
        with c:
            c.execute("ALTER TABLE notify_pushover_pending ADD COLUMN call_at REAL")
    return c


def escalate_after_min() -> int:
    from navig.notify import prefs

    try:
        return max(0, int(prefs.get_raw("pushover_escalate_min", str(ESCALATE_DEFAULT_MIN))))
    except (TypeError, ValueError):
        return ESCALATE_DEFAULT_MIN


def record_pending(receipt: str, type_key: str, title: str, body: str, *, now: float | None = None) -> None:
    import time

    minutes = escalate_after_min()
    from navig.notify import call as voice

    call_minutes = voice.call_after_min()
    # No SMS window and no call configured → there is nothing to escalate to, and
    # a row nobody acts on is just garbage.
    if minutes <= 0 and call_minutes is None:
        return
    now = time.time() if now is None else now
    c = _db()
    with c:
        c.execute(
            "INSERT OR IGNORE INTO notify_pushover_pending "
            "(receipt, type, title, body, sent_at, escalate_at, call_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                receipt,
                type_key,
                title,
                body or "",
                now,
                # escalate_at is NOT NULL, so "no SMS stage" is expressed as
                # "not due for a year" rather than NULL — otherwise a disabled
                # SMS window would still fire the moment the emergency lands.
                now + minutes * 60 if minutes > 0 else now + 365 * 86400,
                None if call_minutes is None else now + call_minutes * 60,
            ),
        )


def _claim(c, receipt: str, from_state: str) -> bool:
    """Atomically take a due row, so two processes never both escalate it.

    The caller states which state it read the row in, so a row the SMS stage
    just released cannot be grabbed again by the same stage.
    """
    with c:
        cur = c.execute(
            "UPDATE notify_pushover_pending SET state = 'checking' WHERE receipt = ? AND state = ?",
            (receipt, from_state),
        )
    return cur.rowcount == 1


def _set_state(c, receipt: str, state: str) -> None:
    with c:
        c.execute("UPDATE notify_pushover_pending SET state = ? WHERE receipt = ?", (state, receipt))


async def _escalate_by_sms(type_key: str, title: str, body: str) -> tuple[bool, str]:
    """Send the unacknowledged alert by SMS, regardless of the type's matrix row.

    Awaited on the gateway's own loop: the SMS adapter belongs to it."""
    from navig.notify import prefs
    from navig.notify.router import get_notification_router

    if not prefs.get_target("sms"):
        return False, "no SMS number configured"
    text = f"{body}\n\n(Unacknowledged on Pushover — escalated to SMS.)".strip()
    return await get_notification_router()._send_one("sms", type_key, f"⚠️ {title}", text, "critical", {})


def _due(now: float) -> list[dict[str, Any]]:
    """Rows owing an action, each tagged with the stage: ``sms`` or ``call``.

    The SMS window and the call window are independent offsets from the moment
    the emergency landed, so an install can run either (or both).
    """
    c = _db()
    rows = c.execute(
        "SELECT receipt, type, title, body, state, escalate_at, call_at "
        "FROM notify_pushover_pending "
        "WHERE (state = 'pending' AND escalate_at <= ?) "
        "   OR (state IN ('pending', 'escalated') AND call_at IS NOT NULL AND call_at <= ?)",
        (now, now),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        # The SMS only counts when its window has passed and it has not gone yet.
        sms_due = row["state"] == "pending" and float(row["escalate_at"] or 0) <= now
        stage = "sms" if sms_due else "call"
        if not _claim(c, row["receipt"], row["state"]):
            continue
        row["stage"] = stage
        out.append(row)
    return out


def _finish(receipt: str, state: str, *, now: float) -> None:
    c = _db()
    _set_state(c, receipt, state)
    with c:  # keep the table small: a week of history is plenty
        c.execute("DELETE FROM notify_pushover_pending WHERE state != 'pending' AND sent_at < ?", (now - 7 * 86400,))


def _place_call(type_key: str, title: str, body: str) -> tuple[bool, str]:
    """Telephone the operator. Returns ``(ok, detail)`` — never raises."""
    from navig.notify import call as voice

    text = f"Unacknowledged NAVIG alert: {title}."
    if body:
        text += f" {body}"
    out = voice.place_call(text)
    if not out.ok:
        logger.warning("pushover call escalation failed: %s", out.detail)
    return out.ok, out.detail


async def check_escalations(*, now: float | None = None) -> list[dict[str, Any]]:
    """Act on every due, unacknowledged emergency: SMS first, then a call.

    Two independent windows from the moment the emergency landed, so an install
    can run either stage alone. Returns what it did, receipt by receipt.
    """
    import asyncio
    import time

    now = time.time() if now is None else now
    done: list[dict[str, Any]] = []
    for row in await asyncio.to_thread(_due, now):
        receipt = row["receipt"]
        stage = row.get("stage", "sms")
        held = str(row.get("state") or "pending")
        try:
            status = await asyncio.to_thread(receipt_status, receipt)
        except Exception as exc:  # noqa: BLE001 — Pushover unreachable: try again next tick
            logger.warning("pushover: receipt %s unreadable (%s) — retrying", receipt, exc)
            await asyncio.to_thread(_finish, receipt, held, now=now)
            continue
        if int(status.get("acknowledged") or 0) == 1:
            await asyncio.to_thread(_finish, receipt, "acknowledged", now=now)
            done.append({"receipt": receipt, "result": "acknowledged"})
            continue

        if stage == "call":
            ok, detail = await asyncio.to_thread(
                _place_call, str(row.get("type") or ""), row["title"], row["body"] or ""
            )
            await asyncio.to_thread(_finish, receipt, "called" if ok else "call_failed", now=now)
            done.append({"receipt": receipt, "result": "called" if ok else f"call failed: {detail}"})
            if not ok:
                _record_failed(receipt, row["title"], f"the voice escalation failed: {detail}")
            continue

        try:
            ok, detail = await _escalate_by_sms(row["type"], row["title"], row["body"] or "")
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        await asyncio.to_thread(_finish, receipt, "escalated" if ok else "escalation_failed", now=now)
        if not ok:
            logger.warning("pushover: %s unacknowledged and SMS escalation failed: %s", receipt, detail)
            _record_failed(receipt, row["title"], f"the SMS escalation failed: {detail}")
        done.append({"receipt": receipt, "result": "escalated" if ok else f"failed: {detail}"})
    return done


def _record_failed(receipt: str, title: str, why: str) -> None:
    """Put a failed escalation in the feed — a silent one is the original bug."""
    try:
        from navig.notify import feed

        feed.append(
            "system_alert",
            f"Unacknowledged emergency: {title}",
            f"Pushover was not acknowledged and {why}.",
            priority="high",
            data={"receipt": receipt},
        )
    except Exception:  # noqa: BLE001 — recording must never cost the delivery
        logger.debug("escalation feed note failed", exc_info=True)


async def run_escalation_monitor(interval_s: float = 60.0) -> None:
    """The ``pushover_escalation`` monitor loop. Idle unless an emergency is pending."""
    import asyncio

    while True:
        try:
            await check_escalations()
        except Exception:  # noqa: BLE001 — one bad tick must not end the monitor
            logger.warning("pushover escalation tick failed", exc_info=True)
        await asyncio.sleep(interval_s)


def receipt_status(receipt: str) -> dict[str, Any]:
    """Whether an emergency message was acknowledged (``acknowledged`` = 1)."""
    import requests

    token, _user = credentials()
    resp = requests.get(f"{API}/receipts/{receipt}.json", params={"token": token}, timeout=_TIMEOUT)
    body = resp.json()
    return body if isinstance(body, dict) else {}


#: Types `setup` turns Pushover on for: the rare must-not-miss ones. Everything else
#: stays opt-in from the matrix — an alarm that rings often is soon ignored.
RECOMMENDED_TYPES = ("security_alert", "sales_check_failed")
