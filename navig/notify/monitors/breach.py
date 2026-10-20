"""Breach monitor — the opt-in scan of vault emails against CyberAIGen.

Watches every email username stored in the vault's website logins and, once a
cycle, asks the CyberAIGen connector whether any of them turned up in leak
dumps. New records raise ONE ``breach_alert`` notification per cycle (masked
passwords, counts, a pointer to the CLI) — the full record list never travels
through a notification; it is ``navig vault breach list``.

Opt-in twice over: the monitor only starts when ``monitors.breach.enabled`` is
on, and it cannot do anything until the operator has connected a CyberAIGen
token (``cyberaigen/token`` in the vault). No vault identifier leaves the
machine before both are true.

State (findings + already-notified fingerprints + cursor) lives in the VAULT
under ``breach/state`` — findings contain leaked passwords, and plaintext on
disk would be a downgrade. A locked vault reads as "skip this cycle", never as
"wipe the state".

Config (``monitors.breach.*``): ``interval_hours`` (default 24, min 1),
``batch_size`` (default 25 — emails per cycle; the cursor rotates so a big
vault still gets full coverage while bounding scan credits).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("navig.notify")

STATE_LABEL = "breach/state"
#: Wait after boot before the first scan, so it never competes with startup.
FIRST_CYCLE_DELAY_S = 120
#: Consecutive failed cycles before the operator is told once (then quiet until
#: a success re-arms) — one alert per outage, not one per cycle.
FAIL_ALERT_AFTER = 3
#: Notification body caps: emails listed per alert, sources per email.
_MAX_EMAILS_IN_BODY = 10
_MAX_SOURCES_IN_BODY = 3
#: Kept per email in state, newest first — bounds the vault blob.
_MAX_RECORDS_PER_EMAIL = 100
_DEFAULT_INTERVAL_H = 24
_DEFAULT_BATCH = 25


# ── helpers ──────────────────────────────────────────────────────────────────


def mask_password(value: Any) -> str:
    """A leaked password safe for a notification / default CLI row.

    Re-exported from the connector so there is exactly one masking rule (first
    and last character kept, at most 8 asterisks between; ``Hashed`` /
    ``Encrypted`` are API labels and pass through unchanged).
    """
    from navig.connectors.cyberaigen.connector import mask_password as _mask

    return _mask(value)


def record_fingerprint(email: str, record: dict[str, Any]) -> str:
    """Stable identity of one leak record for dedupe — never the password itself."""
    basis = json.dumps(
        {
            "e": (email or "").lower(),
            "db": record.get("databaseName") or "",
            "d": record.get("createdAt") or "",
            "u": record.get("username") or "",
            "p": record.get("password") or "",
        },
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def extract_emails() -> list[str]:
    """Every email-shaped username in the vault's website logins, sorted + unique."""
    try:
        from navig.vault.logins import list_logins

        found = {
            (li.username or "").strip().lower()
            for li in list_logins()
            if "@" in (li.username or "")
        }
    except Exception as exc:  # noqa: BLE001 — locked/absent vault = no emails to scan
        logger.debug("breach: could not list vault logins: %s", exc)
        return []
    return sorted(found)


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "cursor": 0,
        "notified": {},  # email -> [fingerprint, ...] already announced
        "findings": {},  # email -> [record, ...] newest first
        "last_run": None,
        "last_error": None,
        "failures": 0,
        "alerted": False,
    }


def _vault():
    from navig.vault.core import get_vault

    return get_vault()


def load_state() -> dict[str, Any]:
    """Read the state from the vault; ``{}``-shaped default when absent/unreadable.

    Reads via ``get_bytes`` — the RAW payload. ``get_secret`` parses the payload
    as a single-secret item and hands back the *first value* of the JSON object
    (``version: 1`` → ``"1"``), which silently turned every successful scan into
    "never ran" at the status view.

    An unreadable vault is NOT a fresh state — the caller can't tell "" from
    "locked" here, so :func:`scan_once` distinguishes them by attempting the
    write path and reporting honestly.
    """
    try:
        v = _vault()
        if v is None:
            return _empty_state()
        data = json.loads(v.get_bytes(STATE_LABEL).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        state = _empty_state()
        state.update(data)
        return state
    except Exception as exc:  # noqa: BLE001 — missing label is the common "first run"
        logger.debug("breach: state read fell back to default: %s", exc)
        return _empty_state()


def save_state(state: dict[str, Any]) -> None:
    """Persist state to the vault. Raises when the vault is unavailable — the
    caller reports it instead of pretending the cycle succeeded."""
    v = _vault()
    if v is None:
        raise RuntimeError("the vault is not available — run `navig vault init` first")
    v.put(STATE_LABEL, json.dumps(state, ensure_ascii=False).encode("utf-8"))


def _merge_records(existing: list[dict], new_records: list[dict], email: str) -> list[dict]:
    """Union by fingerprint, newest first, capped per email."""
    seen = {record_fingerprint(email, r) for r in existing if isinstance(r, dict)}
    merged = list(existing)
    for rec in new_records:
        if not isinstance(rec, dict):
            continue
        fp = record_fingerprint(email, rec)
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(rec)
    return merged[:_MAX_RECORDS_PER_EMAIL]


def build_notification(new_by_email: dict[str, list[dict]]) -> tuple[str, str]:
    """One alert for the whole cycle: counts + masked passwords, capped lists.

    The full record list is deliberately NOT in here — it is
    ``navig vault breach list``.
    """
    emails = sorted(new_by_email)
    if len(emails) == 1:
        title = f"Breach found: {emails[0]}"
    else:
        title = f"Breach found — {len(emails)} vault emails"

    lines: list[str] = []
    for email in emails[:_MAX_EMAILS_IN_BODY]:
        records = new_by_email[email]
        sources = []
        for rec in records[:_MAX_SOURCES_IN_BODY]:
            db = str(rec.get("databaseName") or "unknown source")
            day = str(rec.get("createdAt") or "").strip()
            sources.append(f"{db} {day}".strip())
        more = f" (+{len(records) - _MAX_SOURCES_IN_BODY} more)" if len(records) > _MAX_SOURCES_IN_BODY else ""
        pw = mask_password(records[0].get("password"))
        pw_part = f" · password {pw}" if pw else ""
        lines.append(f"• {email} — {len(records)} new: {', '.join(sources)}{more}{pw_part}")
    if len(emails) > _MAX_EMAILS_IN_BODY:
        lines.append(f"… +{len(emails) - _MAX_EMAILS_IN_BODY} more emails")
    lines.append("Full list: navig vault breach list")
    return title, "\n".join(lines)


def _cycle_batch(emails: list[str], state: dict[str, Any], batch_size: int) -> list[str]:
    """The slice this cycle scans, rotating through the cursor (wrapping)."""
    if not emails:
        return []
    cursor = int(state.get("cursor") or 0) % len(emails)
    batch = emails[cursor : cursor + batch_size]
    if len(batch) < batch_size:  # wrap around the end so rotation is seamless
        batch += emails[: batch_size - len(batch)]
    return batch


def _cfg_int(cfg: dict, key: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(cfg.get(key, default)))
    except (TypeError, ValueError):
        return default


# ── one cycle ────────────────────────────────────────────────────────────────


async def scan_once(
    cfg: dict | None = None,
    *,
    emails: list[str] | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Scan a cycle's emails, merge findings into the vault state, notify on new.

    ``emails`` overrides the rotating batch (the CLI's ``--email`` / ``--limit``
    paths). ``notify=False`` (the manual ``navig vault breach check``) stores
    findings but raises no notification — the monitor still announces anything
    not yet in the notified set on its next cycle.

    Returns a JSON-safe summary; never raises for scan-side failures (they come
    back as ``error``), so the monitor loop and the CLI share one honest path.
    """
    cfg = cfg or {}
    summary: dict[str, Any] = {
        "checked": 0,
        "emails": [],
        "new_emails": [],
        "new_records": 0,
        "error": None,
    }

    from navig.connectors import bootstrap as connectors_bootstrap

    state = load_state()

    all_emails = extract_emails() if emails is None else []
    targets = emails if emails is not None else _cycle_batch(
        all_emails, state, _cfg_int(cfg, "batch_size", _DEFAULT_BATCH, 1)
    )
    summary["emails"] = list(targets)
    if not targets:
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = None
        state["failures"] = 0
        state["alerted"] = False
        try:
            save_state(state)
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            summary["error"] = f"state not saved: {exc}"
        return summary

    connectors_bootstrap.ensure_connectors_loaded()
    from navig.connectors.registry import get_connector_registry

    notified: dict[str, list[str]] = dict(state.get("notified") or {})
    findings: dict[str, list[dict]] = dict(state.get("findings") or {})
    new_by_email: dict[str, list[dict]] = {}
    error: str | None = None
    connector = None
    from navig.connectors.cyberaigen.connector import scrub_endpoint

    try:
        connector = get_connector_registry().get("cyberaigen")
        await connector.connect()
    except Exception as exc:  # noqa: BLE001 — not registered / not configured / auth broken
        # Transport/config exceptions can embed the endpoint URL; it must never
        # reach state.last_error, logs, or a notification.
        error = scrub_endpoint(exc)

    if error is None:
        for email in targets:
            try:
                data = await connector.check(email)
            except Exception as exc:  # noqa: BLE001 — one probe's failure ends the cycle
                error = f"{email}: {scrub_endpoint(exc)}"
                break
            records = list(data.get("results") or [])
            merged = _merge_records(findings.get(email) or [], records, email)
            findings[email] = merged

            known = set(notified.get(email) or [])
            fresh = [
                r for r in records
                if isinstance(r, dict) and record_fingerprint(email, r) not in known
            ]
            if fresh:
                new_by_email[email] = fresh
                if notify:
                    # A manual `breach check` (notify=False) only STORES: it must
                    # not mark records announced, or the monitor would never
                    # raise them for something the operator has not seen yet.
                    notified[email] = known | {
                        record_fingerprint(email, r) for r in fresh
                    }
            summary["checked"] += 1

    state["findings"] = findings
    state["notified"] = {k: sorted(v) for k, v in notified.items() if v}
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["last_error"] = error

    # Advance the rotation only when the cycle actually scanned its batch.
    if error is None and emails is None and all_emails:
        state["cursor"] = (int(state.get("cursor") or 0) + len(targets)) % len(all_emails)

    summary["new_emails"] = sorted(new_by_email)
    summary["new_records"] = sum(len(v) for v in new_by_email.values())

    if notify:
        failures = 0 if error is None else int(state.get("failures") or 0) + 1
        state["failures"] = failures
        if error is None:
            state["alerted"] = False

    try:
        save_state(state)
    except Exception as exc:  # noqa: BLE001 — vault write failed: say so, keep going
        summary["error"] = summary["error"] or f"state not saved: {exc}"
        error = error or str(exc)

    if notify and error is None and new_by_email:
        from navig.notify import dispatch

        title, body = build_notification(new_by_email)
        try:
            await dispatch(
                "breach_alert",
                title,
                body,
                priority="high",
                data={
                    "source": "breach",
                    "new_records": summary["new_records"],
                    "emails": summary["new_emails"],
                },
            )
        except Exception as exc:  # noqa: BLE001 — a failed send must not kill the loop
            logger.warning("breach: could not send breach_alert: %s", exc)

    if notify and error is not None:
        if (
            int(state.get("failures") or 0) >= FAIL_ALERT_AFTER
            and not state.get("alerted")
        ):
            from navig.notify import dispatch

            try:
                await dispatch(
                    "system_alert",
                    "Breach check failing",
                    f"The breach monitor has failed {state['failures']} cycles in a row.\n"
                    f"Last error: {error}\n"
                    "Check the token (navig vault breach status) and CyberAIGen service health.",
                    priority="high",
                    data={"source": "breach"},
                )
                state["alerted"] = True
                save_state(state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("breach: could not send failure alert: %s", exc)

    if error is not None:
        summary["error"] = error
    return summary


# ── the loop ─────────────────────────────────────────────────────────────────


async def run_breach_monitor(config: dict | None = None) -> None:
    """Background task: scan on an interval; survive every cycle that fails."""
    cfg = dict(config or {})
    interval_h = _cfg_int(cfg, "interval_hours", _DEFAULT_INTERVAL_H, 1)
    await asyncio.sleep(FIRST_CYCLE_DELAY_S)
    while True:
        try:
            summary = await scan_once(cfg, notify=True)
            if summary.get("error"):
                logger.warning("breach: cycle finished with error: %s", summary["error"])
            else:
                logger.debug(
                    "breach: cycle ok — %s emails, %s new records",
                    summary["checked"],
                    summary["new_records"],
                )
        except Exception:  # noqa: BLE001 — a monitor that dies is a silent monitor
            logger.exception("breach: cycle crashed")
        await asyncio.sleep(interval_h * 3600)
