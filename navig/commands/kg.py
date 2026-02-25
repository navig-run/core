"""
NAVIG Knowledge Graph CLI

Commands:
    navig kg remember <subject> <predicate> <object>   — Store a fact
    navig kg recall <subject>                           — Recall all facts about a subject
    navig kg search <query>                             — Full-text search facts
    navig kg routines                                   — List all registered routines
    navig kg forget <fact-id>                           — Delete a fact
    navig kg status                                     — Show DB stats
"""
from __future__ import annotations

from typing import Optional
import typer

from navig.lazy_loader import lazy_import

_ch = lazy_import("navig.console_helper")
_kg_mod = lazy_import("navig.memory.knowledge_graph")

kg_app = typer.Typer(name="kg", help="Knowledge graph — remember facts, routines, and habits")


def _kg():
    return _kg_mod.get_knowledge_graph()


@kg_app.command("remember")
def kg_remember(
    subject: str = typer.Argument(..., help="Entity (e.g. 'user', 'github.com')"),
    predicate: str = typer.Argument(..., help="Relation (e.g. 'pays_bills_on')"),
    object_: str = typer.Argument(..., metavar="OBJECT", help="Value (e.g. '15th of month')"),
    confidence: float = typer.Option(1.0, "--confidence", "-c", min=0.0, max=1.0),
    source: str = typer.Option("user_statement", "--source", "-s"),
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="Replace existing fact with same subject+predicate"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Store a fact triple in the knowledge graph."""
    import json
    kg = _kg()
    fid = kg.remember_fact(subject, predicate, object_, confidence=confidence, source=source, overwrite=overwrite)
    if json_output:
        from rich import print as rprint
        rprint(json.dumps({"id": fid, "subject": subject, "predicate": predicate, "object": object_}))
    else:
        _ch.success(f"Fact stored: [cyan]{subject}[/cyan] → [yellow]{predicate}[/yellow] → [green]{object_}[/green] (ID: {fid})")


@kg_app.command("recall")
def kg_recall(
    subject: str = typer.Argument(..., help="Subject entity to recall facts about"),
    predicate: Optional[str] = typer.Option(None, "--predicate", "-p", help="Filter by predicate"),
    min_confidence: float = typer.Option(0.0, "--min-confidence"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Recall all facts about a subject."""
    import json
    kg = _kg()
    facts = kg.recall(subject, predicate=predicate, min_confidence=min_confidence)
    if json_output:
        from rich import print as rprint
        rprint(json.dumps([f.to_dict() for f in facts], default=str))
        return
    if not facts:
        _ch.warning(f"No facts found for '{subject}'.")
        return
    from rich.table import Table
    from rich.console import Console
    table = Table(title=f"Facts: {subject}", show_lines=False)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Predicate", style="yellow")
    table.add_column("Object", style="green")
    table.add_column("Conf", justify="right", style="cyan")
    table.add_column("Source", style="dim")
    for f in facts:
        table.add_row(f.id, f.predicate, f.object, f"{f.confidence:.0%}", f.source)
    Console().print(table)


@kg_app.command("search")
def kg_search(
    query: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Full-text search across all facts (subject, predicate, object)."""
    import json
    kg = _kg()
    facts = kg.search_facts(query, limit=limit)
    if json_output:
        from rich import print as rprint
        rprint(json.dumps([f.to_dict() for f in facts], default=str))
        return
    if not facts:
        _ch.warning(f"No facts matching '{query}'.")
        return
    from rich.console import Console
    con = Console()
    con.print(f"[bold]Found {len(facts)} fact(s) for[/bold] \"{query}\":\n")
    for f in facts:
        con.print(f"  [dim]{f.id}[/dim] [cyan]{f.subject}[/cyan] → [yellow]{f.predicate}[/yellow] → [green]{f.object}[/green] ({f.confidence:.0%})")


@kg_app.command("forget")
def kg_forget(
    fact_id: str = typer.Argument(..., help="Fact ID to delete"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a fact by ID."""
    kg = _kg()
    if not force:
        if not _ch.confirm_action(f"Delete fact {fact_id}?"):
            raise typer.Abort()
    if kg.forget_fact(fact_id):
        _ch.success(f"Fact {fact_id} deleted.")
    else:
        _ch.error(f"Fact {fact_id} not found.")
        raise typer.Exit(1)


@kg_app.command("routines")
def kg_routines(
    enabled_only: bool = typer.Option(True, "--enabled/--all"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List all registered routines."""
    import json
    kg = _kg()
    routines = kg.get_routines(enabled_only=enabled_only)
    if json_output:
        from rich import print as rprint
        rprint(json.dumps([r.to_dict() for r in routines], default=str))
        return
    if not routines:
        _ch.warning("No routines found.")
        return
    from rich.table import Table
    from rich.console import Console
    table = Table(title="NAVIG Routines", show_lines=False)
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Schedule", style="yellow")
    table.add_column("Description")
    table.add_column("Last Run", style="dim")
    for r in routines:
        last = r.last_run.strftime("%Y-%m-%d %H:%M") if r.last_run else "—"
        table.add_row(r.id, r.name, r.schedule, r.description or "—", last)
    Console().print(table)


@kg_app.command("status")
def kg_status():
    """Show knowledge graph statistics."""
    kg = _kg()
    import sqlite3
    con = kg._con
    fact_count = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    routine_count = con.execute("SELECT COUNT(*) FROM routines").fetchone()[0]
    from rich.console import Console
    Console().print(
        f"[bold]Knowledge Graph Status[/bold]\n"
        f"  Facts:    [cyan]{fact_count}[/cyan]\n"
        f"  Routines: [cyan]{routine_count}[/cyan]\n"
        f"  DB:       [dim]{kg._path}[/dim]"
    )
