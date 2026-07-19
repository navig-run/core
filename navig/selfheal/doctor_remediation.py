"""navig.selfheal.doctor_remediation — close the observe→repair loop on doctor verdicts.

Consumes the STRUCTURED health report from ``navig.commands.doctor.collect_report()``
(the exact dict ``navig doctor --json`` prints — same process, same check functions,
no subprocess) and maps specific failing checks onto remediations that ALREADY exist
elsewhere in navig. Nothing in this module invents a new repair action:

    Gateway down / MESH_TOKEN unset  → the daemon start path (``navig service start``,
                                       which itself honours the user's stop-intent flag)
    Legacy credentials unmigrated    → ``navig.vault.migrate.migrate_from_legacy()``
                                       (read-only on the legacy DB, skips existing labels)
    Event processor wedged           → REPORT-ONLY: restarting a LIVE daemon interrupts
    Telegram webhook stale tenant    → in-flight work — the operator restarts deliberately
    Leaked debug browsers            → REPORT-ONLY: ``navig cdp stop --all`` may close a
                                       browser another active session is still driving

Safety conventions (same floor as :mod:`navig.selfheal.ssh_healer`):

- an action marked ``safe=False`` is NEVER executed automatically — report-only;
- every executed action returns an outcome; exceptions are caught internally;
- each remediation runs at most once per heal pass, no matter how many failing
  checks map to it;
- output carries counts and states only — never secrets, labels, or payloads.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Seconds to let the daemon settle after an executed action before re-collecting
# the report (the gateway child binds its port a moment after the supervisor's
# PID file appears). Module-level so tests can zero it.
_SETTLE_SECONDS = 3.0


# ──────────────────────────────────────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Remediation:
    """One EXISTING repair path a failing doctor check can map to.

    ``safe=False`` means report-only: the mapping names the fix for the operator
    but :func:`execute` will never run it automatically. ``action`` is a key
    into :data:`_ACTIONS` (kept indirect so tests can substitute callables);
    ``None`` for report-only entries.
    """

    id: str
    title: str
    safe: bool
    hint: str
    action: str | None = None


@dataclass
class PlannedAction:
    """A failing check paired with its remediation (or the lack of one)."""

    section: str
    label: str
    detail: str
    warn: bool
    remediation: Remediation | None
    executed: bool = False
    ok: bool | None = None
    message: str = ""
    healed: bool | None = None  # after re-collect: did the check go green?

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "label": self.label,
            "detail": self.detail,
            "warn": self.warn,
            "remediation": self.remediation.id if self.remediation else None,
            "safe": self.remediation.safe if self.remediation else None,
            "hint": self.remediation.hint if self.remediation else None,
            "executed": self.executed,
            "ok": self.ok,
            "message": self.message,
            "healed": self.healed,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Actions — thin adapters over the EXISTING entry points, imported lazily
# ──────────────────────────────────────────────────────────────────────────────


def _start_daemon() -> tuple[bool, str]:
    """Start the NAVIG daemon via the canonical service path.

    Reuses ``navig.commands.service.service_start`` so every guard that path
    carries applies here too: the deliberate-stop flag (a daemon the user
    stopped on purpose is NOT auto-restarted), the stop-watchdog race guard,
    and the PID-file poll. Starting when nothing is running is purely additive.
    """
    import typer as _typer

    try:
        from navig.commands.service import service_start

        service_start(foreground=False, logs=False)
    except _typer.Exit as exc:
        if (exc.exit_code or 0) != 0:
            return False, "daemon failed to start — check: navig service logs"
    except Exception as exc:  # noqa: BLE001 — a heal action must never crash the heal
        return False, f"daemon start failed ({type(exc).__name__})"

    try:
        from navig.daemon.supervisor import NavigDaemon

        if NavigDaemon.is_running():
            return True, f"daemon running (pid={NavigDaemon.read_pid()})"
        return False, (
            "daemon did not start — a deliberate `navig service stop` blocks "
            "auto-restart; run `navig service start` yourself to override"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"could not verify daemon state ({type(exc).__name__})"


def _migrate_legacy_vault() -> tuple[bool, str]:
    """Run the existing legacy-vault migration entry point.

    ``migrate_from_legacy`` opens the legacy DB read-only, never modifies or
    deletes it, and skips items whose label already exists — the same migration
    that would run automatically on the next vault use. Only counts reach the
    output (labels/providers/payloads never appear in diagnostics).
    """
    try:
        from navig.vault.migrate import migrate_from_legacy

        report = migrate_from_legacy()
        return report.ok(), report.summary()
    except Exception as exc:  # noqa: BLE001
        return False, f"migration failed ({type(exc).__name__})"


# Indirection so tests can swap an action without touching the registry.
_ACTIONS: dict[str, Callable[[], tuple[bool, str]]] = {
    "start_daemon": _start_daemon,
    "migrate_legacy_vault": _migrate_legacy_vault,
}


# ──────────────────────────────────────────────────────────────────────────────
# The mapping — check label → remediation (+ optional detail predicate)
# ──────────────────────────────────────────────────────────────────────────────

_START_DAEMON = Remediation(
    id="start-daemon",
    title="start the NAVIG daemon",
    safe=True,
    hint="navig service start",
    action="start_daemon",
)

# Restarting a LIVE daemon kills in-flight chats/jobs — and `navig gateway start`
# force-kills matching gateways (config-dir scoped, but still the operator's
# running brain). That is a deliberate operator decision, never an auto-action.
_RESTART_DAEMON = Remediation(
    id="restart-daemon",
    title="restart the NAVIG daemon",
    safe=False,
    hint="navig service restart",
)

_MIGRATE_LEGACY_VAULT = Remediation(
    id="migrate-legacy-vault",
    title="migrate legacy credentials into the vault",
    safe=True,
    hint="happens automatically on the next vault use",
    action="migrate_legacy_vault",
)

# `navig cdp stop --all` only closes NAVIG-launched browsers, but it cannot tell
# a leaked one from one another active session is still driving — ambiguous, so
# the operator pulls the trigger.
_STOP_LEAKED_BROWSERS = Remediation(
    id="stop-leaked-browsers",
    title="close NAVIG-launched debug browsers",
    safe=False,
    hint="navig cdp stop --all",
)


def _detail_has(*needles: str) -> Callable[[dict[str, Any]], bool]:
    def _applies(check: dict[str, Any]) -> bool:
        detail = str(check.get("detail") or "")
        return any(needle in detail for needle in needles)

    return _applies


# label → (remediation, applies-predicate | None). Only consulted for FAILING
# checks (ok=False), so fail-only labels need no predicate. Predicates narrow
# labels that fail for more than one reason (e.g. "Event processor" also fails
# as "not checked — gateway not running", which the Gateway mapping covers).
_LABEL_MAP: dict[str, tuple[Remediation, Callable[[dict[str, Any]], bool] | None]] = {
    "Gateway": (_START_DAEMON, None),
    "MESH_TOKEN": (_START_DAEMON, None),  # minted on gateway start
    "Legacy credentials": (_MIGRATE_LEGACY_VAULT, None),
    "Event processor": (_RESTART_DAEMON, _detail_has("NOT RUNNING", "emit backlog growing")),
    "Telegram webhook": (_RESTART_DAEMON, _detail_has("STALE tenant")),
    "Leaked browsers": (_STOP_LEAKED_BROWSERS, None),
}


# ──────────────────────────────────────────────────────────────────────────────
# Plan → execute → re-check
# ──────────────────────────────────────────────────────────────────────────────


def plan(report: dict[str, Any]) -> list[PlannedAction]:
    """Every failing check in *report*, paired with its mapped remediation.

    Passing checks are never planned. A failing check whose label is unmapped
    (or whose predicate does not apply) is still listed — with
    ``remediation=None`` — so the heal view shows the WHOLE failure surface,
    not just the part it can act on.
    """
    actions: list[PlannedAction] = []
    for section in report.get("sections") or []:
        for check in section.get("checks") or []:
            if check.get("ok"):
                continue
            remediation: Remediation | None = None
            entry = _LABEL_MAP.get(str(check.get("label") or ""))
            if entry is not None:
                candidate, applies = entry
                if applies is None or applies(check):
                    remediation = candidate
            actions.append(
                PlannedAction(
                    section=str(section.get("name") or ""),
                    label=str(check.get("label") or ""),
                    detail=str(check.get("detail") or ""),
                    warn=bool(check.get("warn")),
                    remediation=remediation,
                )
            )
    return actions


def execute(actions: list[PlannedAction], *, dry_run: bool = False) -> list[PlannedAction]:
    """Run the safe remediations in *actions* (none when *dry_run*).

    Report-only (``safe=False``) remediations are never executed — they get
    their manual hint as the message. Each remediation id runs at most once
    per pass; every action that shares it inherits the outcome.
    """
    outcomes: dict[str, tuple[bool, str]] = {}
    for action in actions:
        remediation = action.remediation
        if remediation is None:
            action.message = "no automatic remediation"
            continue
        if not remediation.safe:
            action.message = f"report-only — fix manually: {remediation.hint}"
            continue
        if dry_run:
            action.message = f"would {remediation.title}"
            continue
        if remediation.id not in outcomes:
            fn = _ACTIONS.get(remediation.action or "")
            if fn is None:  # registry drift — never crash the heal pass
                action.message = f"remediation unavailable — fix manually: {remediation.hint}"
                continue
            try:
                outcomes[remediation.id] = fn()
            except Exception as exc:  # noqa: BLE001 — heal must never crash doctor
                outcomes[remediation.id] = (False, f"failed ({type(exc).__name__})")
        ok, message = outcomes[remediation.id]
        action.executed = True
        action.ok = ok
        action.message = message
    return actions


def _mark_healed(actions: list[PlannedAction], after: dict[str, Any]) -> None:
    """Record, per executed action, whether its check went green in *after*."""
    states: dict[tuple[str, str], bool] = {}
    for section in after.get("sections") or []:
        for check in section.get("checks") or []:
            states[(str(section.get("name") or ""), str(check.get("label") or ""))] = bool(
                check.get("ok")
            )
    for action in actions:
        if action.executed:
            action.healed = states.get((action.section, action.label))


# ──────────────────────────────────────────────────────────────────────────────
# CLI orchestration (navig doctor --heal [--dry-run] [--json])
# ──────────────────────────────────────────────────────────────────────────────


def _summary_line(summary: dict[str, Any]) -> str:
    return (
        f"{summary.get('passed', 0)} passed · {summary.get('warnings', 0)} warnings · "
        f"{summary.get('failed', 0)} failed"
    )


def _print_human(
    actions: list[PlannedAction],
    before: dict[str, Any],
    after: dict[str, Any] | None,
    *,
    dry_run: bool,
) -> None:
    from navig import console_helper as ch
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Section", no_wrap=True, style="dim")
    table.add_column("Check", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Remediation", no_wrap=True)
    table.add_column("Result")  # the one wrappable free-text column

    # Column discipline: Remediation stays a short status verb (no_wrap);
    # Result is the single wrappable free-text column carrying the prose.
    for action in actions:
        state = "[yellow]⚠ warning[/yellow]" if action.warn else "[red]✗ failing[/red]"
        if action.remediation is None:
            remediation = "[dim]○ —[/dim]"
            result = "[dim]no automatic remediation[/dim]"
        elif not action.remediation.safe:
            remediation = "[yellow]report-only[/yellow]"
            result = f"{action.remediation.title} — run: {action.remediation.hint}"
        elif dry_run:
            remediation = "[cyan]auto[/cyan]"
            result = f"[dim]{action.message}[/dim]"
        elif action.executed:
            remediation = "[cyan]auto[/cyan]"
            outcome = "[green]● ran[/green]" if action.ok else "[red]✗[/red]"
            result = f"{outcome} {action.message}"
            if action.healed is True:
                result += " · [green]healed[/green]"
            elif action.healed is False:
                result += " · [yellow]still failing[/yellow]"
        else:
            remediation = "[cyan]auto[/cyan]"
            result = action.message
        table.add_row(action.section, action.label, state, remediation, result)

    ch.get_console().print(table)

    if after is not None:
        ch.info(
            f"before: {_summary_line(before['summary'])} → after: {_summary_line(after['summary'])}"
        )

    # One-line next-step nudge (house style).
    safe_count = sum(1 for a in actions if a.remediation and a.remediation.safe)
    manual = sum(1 for a in actions if a.remediation and not a.remediation.safe)
    if dry_run and safe_count:
        ch.info(f"dry run — re-run without --dry-run to apply {safe_count} safe fix(es)")
    elif manual:
        ch.info(f"{manual} item(s) need a manual decision — see the hints above")
    elif not dry_run:
        ch.info("re-run `navig doctor` any time to confirm")


def run_heal(
    *,
    port: int | None = None,
    skip_deps: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
) -> int:
    """Collect → map → execute safe fixes → re-collect → report before/after.

    Returns the exit code: 0 only when the FINAL report (after healing, when
    anything ran) is fully green — same parity rule as plain ``navig doctor``.
    """
    from navig.commands.doctor import collect_report

    before = collect_report(port=port, skip_deps=skip_deps, quiet=json_output)
    actions = plan(before)

    after: dict[str, Any] | None = None
    if actions:
        execute(actions, dry_run=dry_run)
        if any(a.executed for a in actions):
            if _SETTLE_SECONDS:
                time.sleep(_SETTLE_SECONDS)
            after = collect_report(port=port, skip_deps=skip_deps, quiet=json_output)
            _mark_healed(actions, after)

    final = after or before

    if json_output:
        import json as _json

        payload: dict[str, Any] = {
            "ok": final["ok"],
            "dry_run": dry_run,
            "actions": [a.to_dict() for a in actions],
            "before": before["summary"],
            "after": after["summary"] if after else None,
            "report": final,
        }
        print(_json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if final["ok"] else 1

    from navig import console_helper as ch

    if not actions:
        ch.success("All checks passed — nothing to heal.")
        return 0

    _print_human(actions, before, after, dry_run=dry_run)
    return 0 if final["ok"] else 1
