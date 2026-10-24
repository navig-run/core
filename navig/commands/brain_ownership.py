"""``navig brain status|claim|release|takeover|verify|migrate`` (+ ``telegram``, ``roles``).

These are the operator's controls for Brain ownership. They are registered onto the
existing ``brain`` Typer app (which already owns ``brain prompts``) rather than
introducing a second ``brain`` namespace, so ``navig brain --help`` stays one place.

House style: a Rich table for the human view, ``--json`` for anything a script or the
OS UI parses, and a one-line next step at the end of every status view.

Exit codes matter here and are part of the contract:

* ``0``  the command did what it said
* ``1``  a normal failure (bad arguments, nothing to do)
* ``77`` this machine may not be the Brain (see
  :data:`navig.brain.lease.EXIT_OWNERSHIP_REFUSED`) — distinct so a launcher can tell
  a refusal from a crash, and non-zero so a refusing service is visible.
"""

from __future__ import annotations

from typing import Any

import typer

from navig.brain import authority as _authority
from navig.brain import capabilities as _caps
from navig.brain import guard as _guard
from navig.brain import lease as _lease
from navig.brain import roles as _roles
from navig.brain import telegram_mode as _tg
from navig.brain.identity import hostname, installation_fingerprint, machine_id_source
from navig.brain.identity import owner_id as _owner_id

__all__ = ["register_ownership_commands"]


# ── shared rendering helpers ────────────────────────────────────────────────


def _fmt_ts(value: Any) -> str:
    """A unix float as a short local timestamp — never a bare number in a table."""
    try:
        from datetime import datetime

        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def _lease_rows(status: dict[str, Any]) -> list[tuple[str, str]]:
    owner = status.get("owner") or {}
    you = status.get("you") or {}
    state = str(status.get("state") or "free")
    glyph = {
        "held": "[green]● held[/green]",
        "expired": "[yellow]● expired[/yellow]",
        "released": "[dim]○ released[/dim]",
        "free": "[dim]○ free[/dim]",
    }.get(state, state)
    rows = [
        ("Lease", glyph),
        ("Owner", (f"{owner.get('hostname') or '—'} ({owner.get('id')})") if owner else "—"),
        ("Role", f"{you.get('role') or '—'}" + (" · this machine" if you.get("is_owner") else "")),
        ("Epoch", str(status.get("epoch") or 0)),
    ]
    auth = status.get("authority") or {}
    if auth:
        if str(auth.get("mode") or "local") == "remote":
            if auth.get("reachable") is False:
                record = f"[red]✗ unreachable[/red] {auth.get('url') or ''}"
            else:
                record = f"[green]● shared authority[/green] {auth.get('url') or ''}"
        else:
            record = "[dim]local record — single-machine install[/dim]"
        rows.insert(0, ("Record", record))
    if owner:
        rows.append(("Heartbeat", f"{_fmt_ts(owner.get('heartbeat_at'))}"))
        rows.append(("TTL left", f"{owner.get('expires_in_s')}s"))
    if status.get("summary"):
        rows.append(("Summary", str(status["summary"])))
    return rows


def _print_lease_table(status: dict[str, Any], *, title: str = "Brain ownership") -> None:
    from navig.console_helper import Table, console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value")  # the one free-text column
    for label, value in _lease_rows(status):
        table.add_row(label, value)
    console.print()
    console.print(f"[bold]{title}[/bold]")
    console.print(table)


def _telegram_rows(status: dict[str, Any]) -> list[tuple[str, str]]:
    mode = str(status.get("mode") or _tg.MODE_OFF)
    return [
        ("Delivery", f"[bold]{mode}[/bold]"),
        ("Owner", status.get("owner_id") or "[dim]—[/dim]"),
        ("Epoch", str(status.get("epoch") or 0)),
        ("Updated", str(status.get("updated_at") or "—")),
        ("Transitions", str(len(status.get("transitions") or []))),
    ]


def _print_telegram_table(status: dict[str, Any]) -> None:
    from navig.console_helper import Table, console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value")
    for label, value in _telegram_rows(status):
        table.add_row(label, value)
    console.print()
    console.print("[bold]Telegram delivery[/bold]")
    console.print(table)


def _finish(status: dict[str, Any], ch_mod) -> None:
    """The one-line next step every status view ends with."""
    authority = status.get("authority") or {}
    if str(authority.get("mode") or "") == "remote" and authority.get("reachable") is False:
        ch_mod.dim(
            "Next step: restore the Lighthouse edge (navig lighthouse status) — the owner "
            "record cannot be read or changed while the authority is unreachable."
        )
    elif status.get("state") in ("free", "released"):
        ch_mod.dim("Next step: navig brain claim — make this machine the Brain.")
    elif (status.get("you") or {}).get("is_owner"):
        ch_mod.dim(
            "Next step: navig brain verify · or migrate it: navig brain migrate --to <host> --dry-run"
        )
    elif status.get("can_takeover"):
        owner = (status.get("owner") or {}).get("id")
        ch_mod.dim(
            f"Next step: navig brain takeover --from {owner} --reason <why> — that lease has "
            "expired."
        )
    else:
        ch_mod.dim(
            "Next step: this machine is a client of that Brain. Inspect with navig brain roles."
        )


def _fail(*, json_output: bool, message: str, details: str = "", code: int = 1):
    """Report a failure on the RIGHT stream.

    In ``--json`` mode stdout belongs to exactly one JSON document, so the human line
    goes into the payload instead of the console (a ``ch.error`` here would corrupt
    stdout for the consumer). Returns the ``typer.Exit`` to raise.
    """
    from navig import console_helper as ch

    if json_output:
        # `error` carries the WHOLE reason (a script prints it to a human), `summary`
        # is the short label the table would have shown.
        ch.emit_json(
            {
                "ok": False,
                "error": details or message,
                "summary": message,
                "exit_code": code,
            }
        )
    else:
        ch.error(message, details or None)
    return typer.Exit(code)


# ── status ──────────────────────────────────────────────────────────────────


def brain_status(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable ownership state."),
) -> None:
    """Who owns this Brain, what role this machine has, and what it may do."""
    from navig import console_helper as ch

    store = _authority.lease_store()
    status = store.status()
    telegram = _tg.TelegramModeStore().status()
    payload = {
        "ok": True,
        "owner_id": _owner_id(),
        "hostname": hostname(),
        "brain": status,
        "telegram": telegram,
        # Identity is a FINGERPRINT, never the key: a migration compares it end to end
        # (`navig brain migrate` calls this on the target), and a receipt records it.
        "identity": {
            "deck_key_fingerprint": _caps.config_section("deck").get("api_key")
            and installation_fingerprint(str(_caps.config_section("deck").get("api_key"))),
            "owner_id": _owner_id(),
        },
        "host_dependency": _roles.host_dependency_warning(
            str((status.get("you") or {}).get("role") or "all-in-one")
        ),
    }
    if json_output:
        ch.emit_json(payload)
        return

    _print_lease_table(status)
    _print_telegram_table(telegram)
    warning = payload["host_dependency"]
    if warning:
        ch.warning("Host dependency", warning)
    _finish(status, ch)


# ── claim / release / takeover ─────────────────────────────────────────────


def brain_claim(
    role: str = typer.Option(
        "",
        "--role",
        "-r",
        help="brain | all-in-one | client | executor (default: this install's configured role)",
    ),
    reason: str = typer.Option("", "--reason", help="Why this machine should own the Brain."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable result."),
) -> None:
    """Make this machine the Brain — only if the lease is free or already ours."""
    from navig import console_helper as ch

    chosen = _lease.normalize_role(role) if role else None
    if chosen is not None and chosen not in _lease.SINGLETON_ROLES:
        raise _fail(
            json_output=json_output,
            message=f"{chosen} cannot own a Brain",
            details="A client/executor has no brain identity. Use --role brain or "
            "--role all-in-one.",
        )
    try:
        lease = _authority.lease_store().acquire(role=chosen, reason=reason or "claimed from the CLI")
    except _lease.OwnershipConflict as exc:
        raise _fail(json_output=json_output, message="Refused", details=str(exc), code=exc.exit_code) from None
    except _lease.BrainOwnershipError as exc:
        raise _fail(json_output=json_output, message="Refused", details=str(exc)) from None

    if json_output:
        ch.emit_json({"ok": True, "lease": lease.to_dict()})
        return
    ch.success(
        f"This machine now owns the Brain (epoch {lease.epoch}, role {lease.role})",
        f"{hostname()} ({lease.owner_id})",
    )
    warning = _roles.host_dependency_warning(lease.role)
    if warning:
        ch.warning("Host dependency", warning)
    ch.dim("Next step: navig service start — the daemon and the bot will follow this lease.")


def brain_release(
    reason: str = typer.Option("", "--reason", help="Why the Brain is being released."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable result."),
) -> None:
    """Give the Brain up cleanly, so another machine can claim it."""
    from navig import console_helper as ch

    try:
        lease = _authority.lease_store().release(reason=reason or "released from the CLI")
    except _lease.BrainOwnershipError as exc:
        raise _fail(json_output=json_output, message="Refused", details=str(exc)) from None
    if json_output:
        ch.emit_json({"ok": True, "lease": lease.to_dict()})
        return
    ch.success(f"Brain released (epoch {lease.epoch} stays recorded)")
    ch.dim("Next step: navig brain claim on the machine that should own it.")


def brain_takeover(
    from_owner: str = typer.Option(
        "", "--from", help="The owner you intend to displace (required to displace a live Brain)."
    ),
    reason: str = typer.Option(..., "--reason", help="Why the Brain is moving (audited)."),
    role: str = typer.Option("", "--role", help="Role for this machine afterwards."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Displace a lease that is still heartbeating. Requires --from.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable result."),
) -> None:
    """Take the Brain over explicitly: bumps the epoch and records who/why in the audit."""
    from navig import console_helper as ch

    try:
        lease = _authority.lease_store().takeover(
            role=_lease.normalize_role(role) if role else None,
            reason=reason,
            from_owner=from_owner or None,
            force=force,
        )
    except _lease.OwnershipConflict as exc:
        raise _fail(json_output=json_output, message="Refused", details=str(exc), code=exc.exit_code) from None
    except _lease.BrainOwnershipError as exc:
        raise _fail(json_output=json_output, message="Refused", details=str(exc)) from None

    if json_output:
        ch.emit_json({"ok": True, "lease": lease.to_dict()})
        return
    audit = lease.takeovers[-1] if lease.takeovers else {}
    ch.success(
        f"Brain taken over (epoch {lease.epoch})",
        f"from {audit.get('from_owner') or '—'} · reason: {audit.get('reason') or reason}",
    )
    ch.dim(
        "Every older writer now presents a stale epoch and is refused. "
        "Next step: navig service restart on this machine."
    )


# ── verify ─────────────────────────────────────────────────────────────────


def brain_verify(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable verification."),
) -> None:
    """Prove this Brain is the owner AND that its capabilities are really there.

    ``navig brain migrate`` calls this ON the target, so a green line here is what
    replaces "the files were copied" as evidence.
    """
    from navig import console_helper as ch

    store = _authority.lease_store()
    status = store.status()
    decision = _guard.check(service="daemon", store=store)
    caps = _caps.collect_capabilities()

    unverified = [
        name
        for name, report in caps.items()
        if not report.get("verified") and report.get("applicable", True)
    ]
    applicable = [n for n, r in caps.items() if r.get("applicable", True)]
    payload = {
        "ok": bool(decision.allowed and not unverified),
        "owner_id": _owner_id(),
        "hostname": hostname(),
        "machine_id_source": machine_id_source(),
        "role": status["you"]["role"],
        "ownership": decision.as_dict(),
        "brain": status,
        "capabilities": caps,
        "unverified": unverified,
        "applicable": applicable,
    }
    if json_output:
        ch.emit_json(payload)
        if not payload["ok"]:
            raise typer.Exit(_lease.EXIT_OWNERSHIP_REFUSED if not decision.allowed else 1)
        return

    from navig.console_helper import Table, console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Capability", style="bold", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Detail")
    for name, report in caps.items():
        if report.get("verified"):
            state = "[green]✓ verified[/green]"
        elif not report.get("applicable", True):
            state = "[dim]○ not used[/dim]"
        else:
            state = "[red]✗ unverified[/red]"
        table.add_row(name, state, str(report.get("detail") or ""))
    console.print()
    console.print(f"[bold]Brain verification[/bold] — {hostname()} · role {status['you']['role']}")
    console.print(table)

    if not decision.allowed:
        ch.error("Not the Brain here", decision.message)
        raise typer.Exit(decision.exit_code or _lease.EXIT_OWNERSHIP_REFUSED)
    if unverified:
        ch.warning(
            "Capabilities unverified",
            f"{', '.join(unverified)} — a migration must NOT report complete until these pass.",
        )
        raise typer.Exit(1)
    ch.success("This Brain owns the install and its capabilities are verified")


# ── migrate ────────────────────────────────────────────────────────────────


def brain_migrate(
    to: str = typer.Option("", "--to", help="Host that will become the Brain."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan and the stages; change nothing."
    ),
    resume: str = typer.Option("", "--resume", help="Receipt id to resume after an interruption."),
    bundle: str = typer.Option(
        "",
        "--bundle",
        help="An archive you exported yourself (navig backup export --brain). Its digest is "
        "recorded and verified on the target instead of re-exporting.",
    ),
    rotate_identity: bool = typer.Option(
        False,
        "--rotate-identity",
        help="Allow the target's deck key fingerprint to differ (a deliberate re-identification).",
    ),
    history: bool = typer.Option(False, "--history", help="List recorded migration receipts."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable receipt(s)."),
) -> None:
    """Move the Brain: Discover → Inspect → Back up → Transfer → Validate → Cut over → Verify → Complete.

    Completion is a VERIFIED claim: copied files and installed packages never count,
    and the receipt refuses to say complete until the remote Brain proves Telegram,
    the scheduler, the mail watcher and the API — with the source fenced.
    """
    from navig import console_helper as ch
    from navig.brain.migrate import (
        STAGES,
        MigrationBlocked,
        MigrationRunner,
        MigrationStore,
        SshRemote,
    )

    store = MigrationStore()
    if history:
        receipts = store.list()
        if json_output:
            ch.emit_json({"ok": True, "receipts": [r.to_dict() for r in receipts]})
            return
        if not receipts:
            ch.info("No migrations recorded yet", str(store.directory))
            return
        from navig.console_helper import Table, console

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Id", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Target", no_wrap=True)
        table.add_column("Updated", no_wrap=True)
        table.add_column("Blockers")
        for receipt in receipts:
            table.add_row(
                receipt.id,
                receipt.state,
                str(receipt.target.get("host") or "—"),
                str(receipt.updated_at or "—"),
                "; ".join(receipt.blockers) or "[dim]—[/dim]",
            )
        console.print(table)
        ch.dim("Next step: navig brain migrate --resume <id> to continue one.")
        return

    if resume:
        existing = store.load(resume)
        target = to or (str(existing.target.get("host") or "") if existing else "")
        if not target:
            raise _fail(
                json_output=json_output,
                message="Cannot resume",
                details="That receipt is missing; pass --to <host> as well.",
            )
    elif not to:
        raise _fail(
            json_output=json_output,
            message="Nothing to do",
            details="Pass --to <host> (or --history / --resume <id>).",
        )

    remote = SshRemote(to or "unknown")
    runner = MigrationRunner(
        remote,
        store=store,
        dry_run=dry_run,
        rotate_identity=rotate_identity,
        bundle=bundle or None,
    )
    loading = store.load(resume) if resume else None
    if dry_run:
        plan = runner.plan(target_host=to, reason=f"migrate to {to}")
        if json_output:
            ch.emit_json({"ok": True, "dry_run": True, "receipt": plan.to_dict()})
            return
        from navig.console_helper import Table, console

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("#", no_wrap=True)
        table.add_column("Stage", style="bold", no_wrap=True)
        table.add_column("What it will do")
        descriptions = {
            "discover": f"reach {to} and confirm it can host a Brain",
            "inspect": "compare roles, identity fingerprint, scheduler and Telegram state",
            "backup": "export this Brain (config, state, vault separately) and checksum it",
            "transfer": "send the bundle and verify the digest end to end",
            "validate": "reconcile counts and prove the identity is preserved",
            "cutover": "fence the source (moved marker + lease release) and start the target",
            "verify": "prove Telegram, scheduler, mail watcher and API on the target",
            "complete": "write the receipt — ONLY if every required capability verified",
        }
        for index, stage in enumerate(STAGES, start=1):
            table.add_row(str(index), stage, descriptions.get(stage, ""))
        console.print()
        console.print(f"[bold]Migration plan[/bold] — {hostname()} → {to} (dry run)")
        console.print(table)
        ch.dim(
            "Nothing was changed. Next step: run again without --dry-run when the source can "
            "be stopped."
        )
        return

    try:
        receipt = runner.run(
            target_host=to,
            reason=f"migrate to {to}",
            resume=loading if loading is not None else None,
        )
    except MigrationBlocked as exc:
        failed = store.latest()
        if json_output:
            ch.emit_json(
                {
                    "ok": False,
                    "error": str(exc),
                    "receipt": failed.to_dict() if failed else None,
                }
            )
        else:
            ch.error("Migration blocked", str(exc))
            if failed is not None:
                ch.dim(
                    f"Receipt: {store.path_for(failed.id)} — resume with "
                    f"`navig brain migrate --resume {failed.id} --to {to}`"
                )
        raise typer.Exit(1) from None

    if json_output:
        ch.emit_json({"ok": receipt.state == "complete", "receipt": receipt.to_dict()})
        if receipt.state != "complete":
            raise typer.Exit(1)
        return
    ch.success(
        f"Migration {receipt.state}",
        f"receipt {store.path_for(receipt.id)} · target {receipt.target.get('host')}",
    )
    if receipt.blockers:
        for blocker in receipt.blockers:
            ch.warning("Blocker", blocker)
        ch.dim("Not complete. Fix the blockers, then resume with --resume " + receipt.id)


# ── telegram mode ──────────────────────────────────────────────────────────


def _run_transition(mode: str, *, drop_pending: bool, json_output: bool) -> None:
    """Drive a mode transition with the REAL Telegram API only when asked."""
    import asyncio

    from navig import console_helper as ch

    # `_cli_telegram_api` IS the Bot API callable the transition awaits
    # (`await api("deleteWebhook", {...})`). Calling it here instead would pass its
    # RESULT — a TypeError at the first call, or "did not happen" if it were wrapped
    # in a try/except, which is exactly how a transition silently stops working.
    api = _cli_telegram_api
    result = asyncio.run(
        _tg.transition(
            mode,
            api=api,
            reason=f"navig brain telegram {mode}",
            drop_pending=drop_pending,
            epoch=_guard.current_epoch(),
        )
    )
    if json_output:
        ch.emit_json({"ok": result.ok, "transition": result.as_dict()})
        if not result.ok:
            raise typer.Exit(1)
        return
    if result.ok:
        ch.success(f"Telegram delivery is now {result.mode}", result.detail)
        ch.dim("Next step: navig brain verify — delivery is part of the migration gate.")
    else:
        ch.error("Transition incomplete", result.detail)
        raise typer.Exit(1)


async def _cli_telegram_api(method: str, params: dict[str, Any]) -> Any:
    """The real Bot API call used by ``navig brain telegram …``.

    Reads the token the same way the channel does and returns ``None`` on any failure —
    the transition treats ``None`` as "did not happen", never as success.
    """
    import aiohttp  # noqa: PLC0415

    token = str(_caps.config_section("telegram").get("bot_token") or "").strip()
    if not token:
        import os  # noqa: PLC0415

        token = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(url, json=params or {}) as response:
                payload = await response.json(content_type=None)
    except Exception:  # noqa: BLE001 — a failed call is "did not happen", not a crash
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    return payload.get("result")


def brain_telegram(
    mode: str = typer.Argument(
        "status", help="status | polling | webhook — the one delivery mode for this Brain."
    ),
    drop_pending: bool = typer.Option(
        False,
        "--drop-pending",
        help="Discard Telegram's queued backlog (destructive; never done by default).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable result."),
) -> None:
    """Show or transition Telegram delivery ownership (polling vs webhook)."""
    from navig import console_helper as ch

    wanted = str(mode or "status").strip().lower()
    if wanted == "status":
        status = _tg.TelegramModeStore().status()
        if json_output:
            ch.emit_json({"ok": True, "telegram": status})
            return
        _print_telegram_table(status)
        ch.dim(
            "Next step: navig brain telegram polling|webhook — a transition is recorded and "
            "verifies every pending update is consumed exactly once."
        )
        return
    if wanted not in (_tg.MODE_POLLING, _tg.MODE_WEBHOOK):
        raise _fail(
            json_output=json_output,
            message="Unknown mode",
            details=f"{wanted!r} — use status, polling or webhook.",
        )

    try:
        _tg.authorize_delivery_mode(wanted)
    except _tg.TelegramModeRefused as exc:
        raise _fail(
            json_output=json_output,
            message="Refused",
            details=str(exc),
            code=exc.exit_code,
        ) from None

    if not json_output and wanted == _tg.MODE_POLLING:
        ch.info(
            "Transitioning to polling",
            "the webhook is deleted FIRST, then pending updates are drained exactly once",
        )
    _run_transition(wanted, drop_pending=drop_pending, json_output=json_output)


# ── roles ──────────────────────────────────────────────────────────────────


def brain_roles(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable choices."),
) -> None:
    """The four install choices, with what each one means for you."""
    from navig import console_helper as ch

    context = _roles.detect_install_context()
    if json_output:
        ch.emit_json({"ok": True, "choices": list(_roles.CHOICES), "context": context})
        return

    from navig.console_helper import Table, console

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("#", no_wrap=True)
    table.add_column("Choice", style="bold", no_wrap=True)
    table.add_column("Role", no_wrap=True)
    table.add_column("What it means")  # the one wrappable column
    for index, item in enumerate(_roles.CHOICES, start=1):
        marker = "→" if item["role"] == context["role"] else " "
        table.add_row(f"{marker}{index}", str(item["title"]), str(item["role"]), str(item["summary"]))
    console.print()
    console.print(f"[bold]Brain choices[/bold] — this machine: {context['role']}")
    console.print(table)

    for item in _roles.CHOICES:
        console.print(f"  [bold]{item['title']}[/bold] — {item['consequence']}")
    console.print()
    ch.dim(f"Detected: {_roles_summary(context)}")
    warning = _roles.host_dependency_warning(context["role"])
    if warning:
        ch.warning("Host dependency", warning)
    ch.dim("Next step: navig brain claim --role <role> (or navig brain migrate --to <host>).")


def _roles_summary(context: dict[str, Any]) -> str:
    """One line of what is already here, so a choice is never offered blind."""
    bits = [f"config {'present' if context['has_config'] else 'absent'}"]
    if context.get("profile"):
        bits.append(f"profile {context['profile']}")
    lease = context.get("lease") or {}
    bits.append(f"lease {lease.get('state') or 'free'}")
    if context.get("moved"):
        bits.append("MOVED elsewhere (fenced)")
    if context.get("deck_key_fingerprint"):
        bits.append(f"deck key {context['deck_key_fingerprint']}")
    return " · ".join(bits)


# ── registration ───────────────────────────────────────────────────────────


def register_ownership_commands(app: typer.Typer) -> None:
    """Attach the ownership verbs to the ONE ``brain`` app (idempotent)."""
    app.command("status")(brain_status)
    app.command("claim")(brain_claim)
    app.command("release")(brain_release)
    app.command("takeover")(brain_takeover)
    app.command("verify")(brain_verify)
    app.command("migrate")(brain_migrate)
    app.command("telegram")(brain_telegram)
    app.command("roles")(brain_roles)
