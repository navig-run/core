"""Mesh topology management commands."""

from __future__ import annotations

from typing import Any

import typer

from navig import console_helper as ch
from navig.console_helper import get_console

mesh_app = typer.Typer(help="Mesh topology management")

# Session/owner registry (T-036 / NAV-016): `navig mesh agents claim|list|release`.
# Lives under mesh because that is the name the pending spec gave it; the store is
# agent_registry.py (a file), not the mesh peer registry — peers are hosts, these
# are live coding sessions.
agents_app = typer.Typer(
    help="Session/owner registry: which live session holds which role, card, worktree.",
    no_args_is_help=True,
)
mesh_app.add_typer(agents_app, name="agents")


@mesh_app.command()
def status() -> None:
    """Show the current mesh topology and peer status."""
    try:
        from navig.mesh.registry import get_registry

        registry = get_registry()
        peers = registry.list_peers()
    except Exception:  # noqa: BLE001
        peers = []


    con = get_console()

    if not peers:
        con.print("[dim]Mesh: no peers discovered yet.[/dim]")
        con.print("[dim]Start the NAVIG daemon to begin mesh discovery:[/dim] navig service start")
        return

    con.print(f"[bold]Mesh peers:[/bold] {len(peers)} node(s) online\n")
    for peer in peers:
        load_pct = int(getattr(peer, "load", 0) * 100)
        caps = ", ".join(getattr(peer, "capabilities", []) or ["—"])
        con.print(
            f"  [cyan]{peer.hostname}[/cyan]"
            f"  [dim]{getattr(peer, 'gateway_url', '')}[/dim]"
            f"  load={load_pct}%  caps=[{caps}]"
        )


@mesh_app.command()
def peers() -> None:
    """List all known mesh peers (alias for 'mesh status')."""
    status()


# ─── Session/owner registry (T-036) ─────────────────────────────────────────


def _registry() -> Any:
    from navig import agent_registry  # noqa: PLC0415

    return agent_registry


@agents_app.command("claim")
def agents_claim(
    role: str = typer.Option(..., "--role", help="orchestrator | backend-worker | frontend-worker | reviewer"),
    card: str = typer.Option(None, "--card", help="Card this session is taking."),
    worktree: str = typer.Option(None, "--worktree", help="Worktree path this session owns."),
    branch: str = typer.Option(None, "--branch", help="Branch this session is writing."),
    session: str = typer.Option(None, "--session", help="Session id (default: auto-detect)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Claim a role for this session (upsert — one entry per session, refreshed)."""
    ar = _registry()
    try:
        entry = ar.claim(role, session_id=session, card=card, worktree=worktree, branch=branch)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    if as_json:
        ch.emit_json(entry)
        return
    ch.success(f"{entry['role']} claimed", f"session {entry['session_id'][:12]}")
    if entry.get("card"):
        ch.dim(f"  card: {entry['card']}")


@agents_app.command("release")
def agents_release(
    session: str = typer.Option(None, "--session", help="Session id (default: auto-detect)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Release this session's registry claim (kept as history, status=released)."""
    entry = _registry().release(session_id=session)
    if entry is None:
        ch.error("No registry entry to release.", "Claim one: navig mesh agents claim --role <role>")
        raise typer.Exit(1)
    if as_json:
        ch.emit_json(entry)
        return
    ch.success(f"Released {entry.get('role')}", f"session {entry['session_id'][:12]}")


@agents_app.command("list")
def agents_list(
    active_only: bool = typer.Option(False, "--active", help="Only unreleased claims."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Who holds which role — the assignment board's read side."""
    entries = _registry().list_entries(active_only=active_only)

    if as_json:
        ch.emit_json({"agents": entries, "count": len(entries)})
        return

    if not entries:
        ch.info("No registry entries.", "Claim one: navig mesh agents claim --role <role>")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Session", style="dim", no_wrap=True)
    table.add_column("Role", no_wrap=True)
    table.add_column("Card", no_wrap=True)
    table.add_column("Worktree", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Age", no_wrap=True)
    for e in entries:
        age = e.get("age_seconds")
        age_txt = f"{age // 60}m" if isinstance(age, int) else "—"
        status = str(e.get("status") or "")
        if status == "active" and e.get("stale"):
            status = "active (stale)"
        table.add_row(
            str(e.get("session_id", ""))[:12],
            str(e.get("role") or "—"),
            str(e.get("card") or "—"),
            str(e.get("worktree") or "—"),
            status,
            age_txt,
        )
    ch.console.print(table)
