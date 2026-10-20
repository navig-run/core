"""``navig ai fallback`` — see and manage which model answers when a primary fails.

Backed by :mod:`navig.llm.model_fallback` (policy ``llm.fallback.*``, the
degraded state and the switch ledger) — the same data the deck's
Settings → AI & Models → Fallbacks page shows.
"""

from __future__ import annotations

import time

import typer

import navig.console_helper as ch

fallback_app = typer.Typer(
    help="Model fallback — what answers when a mode's model fails, and what never may",
    invoke_without_command=True,
    no_args_is_help=False,
)

_HEALTH_STYLE = {
    "live": "[green]● live[/green]",
    "degraded": "[yellow]▲ degraded[/yellow]",
    "auth": "[red]✗ auth[/red]",
    "dead": "[red]✗ dead[/red]",
    "nokey": "[dim]○ no key[/dim]",
    "unreachable": "[red]✗ unreachable[/red]",
    "unknown": "[dim]○ unknown[/dim]",
}


def _ago(ts: float | None) -> str:
    if not ts:
        return "—"
    s = max(0, int(time.time() - float(ts)))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _valid_modes() -> set[str]:
    from navig.llm.router import CANONICAL_MODES

    return set(CANONICAL_MODES)


def _canonical_mode(mode: str) -> str:
    """Validate a mode name the operator TYPED (aliases allowed). Exits 1 on a typo —
    never silently resolves to big_tasks the way message routing would."""
    from navig.llm.router import CANONICAL_MODES, MODE_ALIASES, LLMModeRouter

    m = (mode or "").strip().lower()
    if m not in CANONICAL_MODES and m not in MODE_ALIASES:
        ch.error(f"Unknown mode: {mode}", f"modes: {', '.join(sorted(CANONICAL_MODES))}")
        raise typer.Exit(1)
    return LLMModeRouter.resolve_mode(m)


def _render_status(data: dict) -> None:
    from navig.console_helper import Table

    pol = data.get("policy") or {}
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Mode", no_wrap=True, style="cyan")
    table.add_column("Primary", no_wrap=True)
    table.add_column("Health", no_wrap=True)
    table.add_column("Falls back to")
    table.add_column("Last switch", no_wrap=True)
    for row in data.get("modes") or []:
        if not row.get("primary"):
            table.add_row(row["mode"], "[red]unresolved[/red]", "—", row.get("error", ""), "—")
            continue
        health = _HEALTH_STYLE.get(row["health"], f"[dim]{row['health']}[/dim]")
        if row.get("never"):
            fb = "[magenta]never — fails loudly[/magenta]"
        elif row.get("degraded"):
            fb = f"[yellow]⇢ {row['effective_fallback']} (in use)[/yellow]"
        elif row.get("effective_fallback"):
            fb = row["effective_fallback"]
        elif row.get("configured_fallback"):
            fb = f"[red]{row['configured_fallback']} — unreachable (no key / not running)[/red]"
        else:
            fb = "[red]nothing — a failure ends the reply[/red]"
        table.add_row(row["mode"], row["primary"], health, fb, _ago(row.get("last_switch")))
    ch.console.print(table)

    deg = data.get("degraded") or {}
    auto = "on" if pol.get("auto", True) else "off"
    parts = [f"auto {auto}"]
    if pol.get("prefer"):
        parts.append("prefer " + ", ".join(pol["prefer"]))
    if pol.get("never_to"):
        parts.append("never to " + ", ".join(pol["never_to"]))
    ch.dim("Policy: " + " · ".join(parts))
    if deg:
        ch.warning(
            f"{len(deg)} primary model(s) degraded — replies are on a fallback. "
            "Re-auth the provider and it switches back by itself."
        )
    else:
        ch.dim(
            "Set one: navig ai fallback set <mode> --to provider:model · "
            "block one: navig ai fallback --never-from <mode>"
        )


@fallback_app.callback()
def fallback_callback(
    ctx: typer.Context,
    never_from: list[str] = typer.Option(
        None, "--never-from", help="Mode that must NEVER switch model (fails loudly). Repeatable."
    ),
    allow: list[str] = typer.Option(
        None, "--allow", help="Undo --never-from for a mode. Repeatable."
    ),
    auto: str | None = typer.Option(
        None, "--auto", help="on|off — use healthy models the liveness checks saw answering"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the status as JSON"),
):
    """Model fallback status — or change the policy with the options."""
    from navig.core.coerce import coerce_bool
    from navig.llm import model_fallback

    changed = False
    if never_from or allow or auto is not None:
        pol = model_fallback.load_policy()
        new_never = list(pol.never_from)
        for m in never_from or []:
            c = _canonical_mode(m)
            if c not in new_never:
                new_never.append(c)
        for m in allow or []:
            c = _canonical_mode(m)
            new_never = [x for x in new_never if x != c]
        auto_val = None
        if auto is not None:
            if str(auto).strip().lower() not in ("on", "off", "true", "false", "1", "0", "yes", "no"):
                ch.error(f"--auto expects on|off, got {auto!r}")
                raise typer.Exit(1)
            auto_val = coerce_bool(auto, default=True)
        try:
            pol = model_fallback.save_policy(never_from=new_never, auto=auto_val)
        except Exception as exc:  # noqa: BLE001
            ch.error("Could not save the fallback policy", str(exc))
            raise typer.Exit(1) from exc
        changed = True
        if not json_out:
            ch.success(
                "Fallback policy saved",
                f"never from: {', '.join(pol.never_from) or '—'} · auto: {'on' if pol.auto else 'off'}",
            )
    if ctx.invoked_subcommand is not None:
        return
    data = model_fallback.status(include_candidates=False)
    if json_out:
        ch.emit_json(data)
        return
    if changed:
        ch.console.print()
    _render_status(data)


@fallback_app.command("status")
def fallback_status(json_out: bool = typer.Option(False, "--json", help="Print as JSON")):
    """Per mode: primary, its health, the fallback in use or ready, last switch."""
    from navig.llm import model_fallback

    data = model_fallback.status()
    if json_out:
        ch.emit_json(data)
        return
    _render_status(data)


@fallback_app.command("set")
def fallback_set(
    mode: str = typer.Argument(..., help="Mode (big_tasks, coding, small_talk, summarize, research)"),
    to: str | None = typer.Option(None, "--to", help="provider:model to fall back to"),
    clear: bool = typer.Option(False, "--clear", help="Remove the mode's configured fallback"),
    json_out: bool = typer.Option(False, "--json", help="Print the result as JSON"),
):
    """Set (or --clear) the model a mode falls back to."""
    from navig.llm import model_fallback

    canonical = _canonical_mode(mode)
    if bool(to) == bool(clear):
        ch.error("Pass exactly one of --to provider:model or --clear")
        raise typer.Exit(1)
    if to and not model_fallback.valid_spec(to):
        ch.error(f"Expected provider:model, got {to!r}", "e.g. --to nvidia:nvidia/nemotron-3-super-120b-a12b")
        raise typer.Exit(1)
    try:
        cfg = model_fallback.set_mode_fallback(canonical, None if clear else to)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001
        ch.error("Could not save the mode fallback", str(exc))
        raise typer.Exit(1) from exc
    spec = model_fallback.norm_spec(to) if to else None
    if json_out:
        ch.emit_json({"ok": True, "mode": canonical, "fallback": spec, "config": cfg})
        return
    if clear:
        ch.success(f"{canonical}: configured fallback removed")
    else:
        ch.success(f"{canonical} falls back to {spec}")
    if canonical in model_fallback.load_policy().never_from:
        ch.warning(
            f"{canonical} is set to never fall back — this has no effect until "
            f"`navig ai fallback --allow {canonical}`"
        )


@fallback_app.command("history")
def fallback_history(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=500, help="Rows to show"),
    json_out: bool = typer.Option(False, "--json", help="Print as JSON"),
):
    """Recent model switches and recoveries (newest first)."""
    from navig.console_helper import Table
    from navig.llm import model_fallback

    rows = model_fallback.history(limit)
    if json_out:
        ch.emit_json(rows)
        return
    if not rows:
        ch.dim("No model switches recorded yet.")
        return
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("When", no_wrap=True)
    table.add_column("Event", no_wrap=True)
    table.add_column("Mode", no_wrap=True, style="cyan")
    table.add_column("From → to")
    table.add_column("Reason", no_wrap=True)
    for r in rows:
        ev = r.get("event")
        ev_txt = "[yellow]⇢ switched[/yellow]" if ev == "fallback" else "[green]✓ recovered[/green]"
        table.add_row(
            _ago(r.get("ts")), ev_txt, str(r.get("mode") or "?"),
            f"{r.get('from') or '?'} → {r.get('to') or '?'}",
            model_fallback.reason_text(r.get("reason")) if ev == "fallback" else str(r.get("reason") or ""),
        )
    ch.console.print(table)
