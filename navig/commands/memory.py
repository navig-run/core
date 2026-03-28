"""navig memory — conversations, key-facts, and session management."""
import typer

memory_app = typer.Typer(help="Manage NAVIG memory: conversations, facts, sessions", no_args_is_help=True)


@memory_app.command("list")
def memory_list(
    kind: str = typer.Option("all", "--kind", "-k", help="Type: all|facts|conversations|sessions"),
):
    """List stored memory entries."""
    from navig import console_helper as ch

    try:
        from navig.memory.knowledge_base import KnowledgeBase

        kb = KnowledgeBase()
        items = kb.list_facts() if hasattr(kb, "list_facts") else []
        if items:
            for item in items[:20]:
                typer.echo(f"  {item}")
        else:
            ch.info("No memory entries found.")
    except Exception as exc:
        ch.warn(f"Memory unavailable: {exc}")


@memory_app.command("clear")
def memory_clear(
    kind: str = typer.Argument("all", help="What to clear: all|facts|conversations"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Clear stored memory."""
    from navig import console_helper as ch

    if not yes:
        typer.confirm(f"Clear {kind} memory?", abort=True)
    ch.warn("navig memory clear is not yet fully implemented.")


@memory_app.command("search")
def memory_search(query: str = typer.Argument(..., help="Search query")):
    """Search memory entries."""
    from navig import console_helper as ch

    try:
        from navig.memory.knowledge_base import KnowledgeBase

        kb = KnowledgeBase()
        results = kb.search(query) if hasattr(kb, "search") else []
        if results:
            for r in results:
                typer.echo(f"  {r}")
        else:
            ch.info("No results found.")
    except Exception as exc:
        ch.warn(f"Memory unavailable: {exc}")
