"""``navig creative`` — capability intelligence: discovery, proposals, build, publish.

The CLI is one of three surfaces over the SAME choke points (the deck route and
the agent tool are the other two): ``build_proposal`` and ``publish_proposal``
hold the Creative-mode gate and every policy check, so the verb here cannot do
what the card cannot.

Discovery verbs (``status`` / ``index`` / ``list`` / ``show``) work in BOTH
modes — capability discovery and reuse advice stay available to Standard
(owner mandate); only ``build`` and ``publish`` refuse outside Creative.
"""

from __future__ import annotations

from typing import Any

import typer

from navig import console_helper as ch

creative_app = typer.Typer(
    help="Proactive capability intelligence — discover, propose, build, publish.",
    invoke_without_command=True,
    no_args_is_help=False,
)

MODE_BANNER = {
    "creative": "[green]● creative[/green]",
    "standard": "[dim]○ standard[/dim]",
}


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "[dim]never[/dim]"
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds // 60)}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def _resolve(proposal_id: str) -> dict[str, Any] | None:
    """Exact id, or a unique prefix (16-hex ids are long to type)."""
    from navig.creative import proposals as prop

    row = prop.get_proposal(proposal_id)
    if row is not None:
        return row
    matches = [p for p in prop.list_proposals() if str(p.get("id", "")).startswith(proposal_id)]
    return matches[0] if len(matches) == 1 else None


def _status_payload() -> dict[str, Any]:
    from navig.creative import status_payload

    payload = dict(status_payload())
    # CLI-only convenience: human-readable scan age string alongside the raw value.
    payload["last_scan"] = _age(payload.get("last_scan_age_s"))
    return payload


def _render_status(payload: dict[str, Any]) -> None:
    from navig.console_helper import Table

    idx = payload["index"]
    pub = payload["publish"]
    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("Agent mode", MODE_BANNER.get(payload["mode"], payload["mode"]))
    table.add_row(
        "Index",
        (
            f"{'[green]fresh[/green]' if idx.get('fresh') else '[yellow]stale[/yellow]'} · "
            f"{idx.get('entries', 0)} entries · built {_age(idx.get('age_s'))}"
            + (f" · [red]{len(idx.get('errors') or {})} source error(s)[/red]" if idx.get("errors") else "")
        ),
    )
    table.add_row("Last scan", payload.get("last_scan") or "[dim]never[/dim]")
    counts = payload["proposals"]["by_status"]
    summary = " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"
    table.add_row("Proposals", f"{payload['proposals']['total']} total ({summary})")
    table.add_row(
        "Publishing",
        f"mode=[bold]{pub['mode']}[/bold]"
        + (f" → {pub['repo']}" if pub["mode"] == "repo" and pub.get("repo") else ""),
    )
    ch.console.print(table)
    if payload["mode"] == "standard":
        ch.info(
            "Standard mode: discovery is available; proactive scans, builds and "
            "publishes need Agent Mode → Creative (Settings → Brain)."
        )
    else:
        ch.info("Next: navig creative list — review open proposals, navig creative index --q <need>")


# ── verbs ────────────────────────────────────────────────────────────────────


@creative_app.callback()
def creative_default(ctx: typer.Context):
    """Capability intelligence at a glance (same as `navig creative status`)."""
    if ctx.invoked_subcommand is None:
        creative_status(json_out=False)


@creative_app.command("status")
def creative_status(json_out: bool = typer.Option(False, "--json", help="Machine-readable output.")):
    """Show mode, index freshness, scan pace, proposals and publish policy."""
    payload = _status_payload()
    if json_out:
        ch.console.print_json(data=payload)
        return
    _render_status(payload)


@creative_app.command("index")
def creative_index(
    query: str = typer.Option("", "--q", help="Token search over name/summary/tags/id."),
    kind: str = typer.Option("", "--kind", help="Filter: command|tool|skill|plugin|module|mcp|connector|agent_tool."),
    limit: int = typer.Option(15, "--limit", "-n", help="Rows to show."),
    build: bool = typer.Option(False, "--build", help="Force a rebuild before querying."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """Query the capability index — what NAVIG can already do, and how."""
    from navig.console_helper import Table
    from navig.creative import index as cap_index

    snap = cap_index.build_index() if build else cap_index.ensure_index()
    kinds = [kind] if kind else None
    hits = cap_index.find(query, kinds=kinds, limit=limit, index=snap)
    if json_out:
        ch.console.print_json(data={"count": len(hits), "entries": hits})
        return
    if not hits:
        ch.warning(f"No capability matches {query!r}.")
        ch.dim("Try fewer tokens, drop --kind, or rebuild with --build.")
        raise typer.Exit(0)
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Kind", no_wrap=True)
    table.add_column("Name", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("How")
    for row in hits:
        state = str((row.get("observed") or {}).get("state") or "?")
        glyph = {"available": "[green]●[/green]", "wired": "[green]●[/green]"}.get(
            state, f"[yellow]{state}[/yellow]"
        )
        how = str((row.get("how_to_invoke") or {}).get("ref") or "")
        table.add_row(str(row.get("kind")), str(row.get("name"))[:48], glyph, how[:70])
    ch.console.print(table)
    ch.dim(f"{len(hits)} of {len(snap.get('entries', []))} entries · rebuild with --build")


@creative_app.command("list")
def creative_list(
    status: str = typer.Option("", "--status", help="Filter: open|accepted|building|built|failed|dismissed."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """List proposals (the same rows the Dashboard card shows)."""
    from navig.console_helper import Table
    from navig.creative import proposals as prop

    rows = prop.list_proposals(status=status or None)
    if json_out:
        ch.console.print_json(data=rows)
        return
    if not rows:
        ch.info("No proposals. The 6-hour Creative scan creates them; try `navig creative status`.")
        raise typer.Exit(0)
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Id", no_wrap=True)
    table.add_column("Verdict", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Problem")
    for row in rows:
        table.add_row(
            str(row.get("id"))[:12],
            str(row.get("verdict")),
            str(row.get("status")),
            str(row.get("problem"))[:70],
        )
    ch.console.print(table)
    ch.dim("navig creative show <id> · build <id> · dismiss <id>")


@creative_app.command("show")
def creative_show(proposal_id: str, json_out: bool = typer.Option(False, "--json")):
    """Show one proposal's full card: evidence, decision, benefit, risk."""
    from navig.console_helper import Table

    row = _resolve(proposal_id)
    if row is None:
        ch.error(f"No proposal matches {proposal_id!r}.")
        raise typer.Exit(1)
    if json_out:
        ch.console.print_json(data=row)
        return
    decision = row.get("decision") or {}
    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("Id", str(row.get("id")))
    table.add_row("Source", str(row.get("source")))
    table.add_row("Verdict", f"[bold]{row.get('verdict')}[/bold] · status {row.get('status')}")
    table.add_row("Problem", str(row.get("problem")))
    for ev in row.get("evidence") or []:
        table.add_row(
            "Evidence",
            f"{ev.get('kind')}: {ev.get('detail')} ({ev.get('count')}× in {ev.get('window')})",
        )
    table.add_row("Reason", str(decision.get("reason") or ""))
    table.add_row("Reuse", ", ".join(row.get("reuse_components") or []) or "[dim]—[/dim]")
    table.add_row("Gap", str(decision.get("genuine_gap") or "[dim]—[/dim]"))
    table.add_row("Benefit", str(row.get("expected_benefit") or ""))
    impact = row.get("impact") or {}
    table.add_row("Risk", f"{impact.get('risk')} · reversible={impact.get('reversible')}")
    if row.get("artifact"):
        table.add_row("Artifact", f"skill:{row['artifact'].get('slug')}")
    if row.get("version_ref"):
        table.add_row("Version", str(row.get("version_ref")))
    if row.get("assumptions"):
        table.add_row("Assumptions", ", ".join(row["assumptions"]))
    ch.console.print(table)
    ch.dim("build <id> · dismiss <id> · publish <id>")


@creative_app.command("build")
def creative_build(proposal_id: str, json_out: bool = typer.Option(False, "--json")):
    """Build a proposal's artifact through the standard skill contracts (Creative only)."""
    from navig.creative import build as build_mod

    row = _resolve(proposal_id)
    if row is None:
        ch.error(f"No proposal matches {proposal_id!r}.")
        raise typer.Exit(1)
    result = build_mod.build_proposal(str(row["id"]))
    if json_out:
        ch.console.print_json(data=result)
        raise typer.Exit(0 if result["ok"] else 1)
    if not result["ok"]:
        ch.error(f"Build refused: {result['reason']}")
        raise typer.Exit(1)
    ch.success(f"Built skill → {result['path']}")
    ch.info(f"Next: navig skill lint {result['path']} · activate it in chat, or publish <id>")


@creative_app.command("dismiss")
def creative_dismiss(proposal_id: str, json_out: bool = typer.Option(False, "--json")):
    """Dismiss a proposal permanently — it will never be re-proposed."""
    from navig.creative import proposals as prop

    row = _resolve(proposal_id)
    if row is None:
        ch.error(f"No proposal matches {proposal_id!r}.")
        raise typer.Exit(1)
    updated = prop.set_status(str(row["id"]), prop.STATUS_DISMISSED)
    if json_out:
        ch.console.print_json(data={"id": row["id"], "status": updated and updated.get("status")})
        return
    ch.success(f"Dismissed {str(row['id'])[:12]} — it will not come back.")


@creative_app.command("publish")
def creative_publish(
    proposal_id: str,
    automatic: bool = typer.Option(False, "--automatic", help="Non-interactive publish (needs creative.publish.auto_push)."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Publish a built capability under the authorized-destination policy."""
    from navig.console_helper import Table
    from navig.creative.policy import publish_proposal

    row = _resolve(proposal_id)
    if row is None:
        ch.error(f"No proposal matches {proposal_id!r}.")
        raise typer.Exit(1)
    result = publish_proposal(str(row["id"]), automatic=automatic)
    if json_out:
        ch.console.print_json(data=result)
        raise typer.Exit(0 if result["ok"] else 1)
    if not result["ok"]:
        ch.error(f"Publish refused ({'blocked by policy' if result.get('blocked') else 'failed'}): {result['reason']}")
        if result.get("checks"):
            table = Table(box=None, show_header=True, padding=(0, 2))
            table.add_column("Check", no_wrap=True)
            table.add_column("Result", no_wrap=True)
            table.add_column("Detail")
            for chk in result["checks"]:
                glyph = "[green]✔[/green]" if chk["ok"] else "[red]✗[/red]"
                table.add_row(chk["key"], glyph, str(chk["detail"])[:80])
            ch.console.print(table)
        raise typer.Exit(1)
    ch.success(f"Published: {result['reason']} → {result.get('version_ref')}")
    ch.info("Version recorded on the proposal; the destination repo now holds the capability.")


@creative_app.command("publish-enable")
def creative_publish_enable(
    repo: str = typer.Option(..., "--repo", help="Local checkout path or github:owner/name."),
    branch: str = typer.Option("main", "--branch", help="Destination branch."),
    auto_push: bool = typer.Option(False, "--auto-push", help="Allow non-interactive publishes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Authorize a private destination for publishing (owner-run; default stays local)."""
    from navig.config import get_config_manager
    from navig.platform.paths import resolve_user_path

    # The operator types this path — resolve against the invocation cwd, never the
    # process cwd (the CLI may have chdir'd into an active space).
    dest = resolve_user_path(repo)
    if not repo.startswith("github:") and not dest.exists():
        ch.error(f"Destination does not exist: {repo}")
        raise typer.Exit(1)
    repo_out = str(dest) if not repo.startswith("github:") else repo
    if not yes and not typer.confirm(
        f"Enable publishing of verified capabilities to {repo_out!r} (branch {branch!r})?"
    ):
        ch.warning("Left unchanged — publishing stays local-only.")
        raise typer.Exit(0)
    cm = get_config_manager()
    cm.set_global("creative.publish.mode", "repo")
    cm.set_global("creative.publish.repo", repo_out)
    cm.set_global("creative.publish.repos", [repo_out])
    cm.set_global("creative.publish.branch", branch)
    cm.set_global("creative.publish.auto_push", bool(auto_push))
    ch.success(f"Publishing enabled → {repo_out} (branch {branch}, auto_push={bool(auto_push)})")
    ch.info("Revoke anytime: navig creative publish-disable")


@creative_app.command("publish-disable")
def creative_publish_disable():
    """Revoke the publishing destination — back to local-only (the default)."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    cm.set_global("creative.publish.mode", "local")
    ch.success("Publishing disabled — capabilities stay on this machine.")
