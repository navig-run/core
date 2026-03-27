"""
NAVIG Links CLI Commands

Manage browser bookmarks with vault credential associations and auto-login hints.

Commands:
    navig links add <url>          — Add a bookmark
    navig links list               — List all bookmarks
    navig links search <query>     — Full-text search across all links
    navig links show <id>          — Show details of a specific link
    navig links open <id>          — Open link in browser (auto-login if vault cred attached)
    navig links edit <id>          — Edit link metadata
    navig links delete <id>        — Delete a bookmark
    navig links tag <id> <tag>     — Add a tag to a link
    navig links import <file>      — Import bookmarks from JSON/Chrome export
"""

from __future__ import annotations

import json

import typer

from navig.lazy_loader import lazy_import

_ch = lazy_import("navig.console_helper")
_links_db_mod = lazy_import("navig.memory.links_db")

links_app = typer.Typer(name="links", help="Manage browser bookmarks with vault auto-login")


def _db():
    return _links_db_mod.get_links_db()


def _Table(*args, **kwargs):
    from rich.table import Table

    return Table(*args, **kwargs)


def _console():
    from rich.console import Console

    return Console()


def _rprint(*args, **kwargs):
    from rich import print as _rp

    _rp(*args, **kwargs)


# ─────────────────────────── add ─────────────────────────────────────────────


@links_app.command("add")
def add_link(
    url: str = typer.Argument(..., help="URL to bookmark"),
    title: str | None = typer.Option(None, "--title", "-t", help="Page title"),
    notes: str | None = typer.Option(None, "--notes", "-n", help="Notes about this link"),
    tags: str | None = typer.Option(None, "--tags", "-T", help="Comma-separated tags"),
    cred: str | None = typer.Option(
        None, "--cred", "-c", help="Vault credential ID for auto-login"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Add a new bookmark. Optionally associate a vault credential for auto-login."""
    db = _db()

    # Check for duplicate
    existing = db.get_by_url(url)
    if existing:
        _ch.warning(
            f"URL already bookmarked (ID: {existing.id}). Use 'navig links edit' to update."
        )
        raise typer.Exit(0)

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    link_id = db.add(url, title=title, notes=notes, tags=tag_list, vault_cred_id=cred)

    if json_output:
        _rprint(json.dumps({"id": link_id, "url": url}))
    else:
        _ch.success(f"Bookmark added! ID: [bold cyan]{link_id}[/bold cyan]")
        if cred:
            _ch.info(f"Auto-login credential: {cred}")


# ─────────────────────────── list ────────────────────────────────────────────


@links_app.command("list")
def list_links(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    cred: str | None = typer.Option(None, "--cred", "-c", help="Filter by vault credential ID"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum number of results"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """List all bookmarks."""
    db = _db()

    if tag:
        links = db.list_by_tag(tag)
    elif cred:
        links = db.list_with_vault_cred(cred)
    else:
        links = db.list_all(limit=limit)

    if json_output:
        _rprint(json.dumps([l.to_dict() for l in links], default=str))
        return

    if not links:
        _ch.warning("No bookmarks found.")
        return

    table = _Table(title="NAVIG Links", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("URL", style="blue", max_width=50, no_wrap=True)
    table.add_column("Title", max_width=30)
    table.add_column("Tags", style="yellow")
    table.add_column("🔑 Cred", style="green")
    table.add_column("Visits", justify="right", style="dim")
    table.add_column("Last Visited", style="dim")

    for link in links:
        last = link.last_visited.strftime("%Y-%m-%d") if link.last_visited else "—"
        table.add_row(
            link.id,
            link.url[:50],
            link.title or "—",
            ", ".join(link.tags) or "—",
            "✅ " + link.vault_cred_id if link.vault_cred_id else "—",
            str(link.visit_count),
            last,
        )

    _console().print(table)


# ─────────────────────────── search ──────────────────────────────────────────


@links_app.command("search")
def search_links(
    query: str = typer.Argument(..., help="Search query (supports FTS5 syntax)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Full-text search bookmarks by URL, title, notes, or tags."""
    db = _db()
    links = db.search(query, limit=limit)

    if json_output:
        _rprint(json.dumps([l.to_dict() for l in links], default=str))
        return

    if not links:
        _ch.warning(f"No bookmarks matching '{query}'.")
        return

    con = _console()
    con.print(f'[bold]Found {len(links)} result(s) for[/bold] "{query}":\n')
    for link in links:
        cred_hint = f" [green]🔑 {link.vault_cred_id}[/green]" if link.vault_cred_id else ""
        tags_hint = f" [yellow][{', '.join(link.tags)}][/yellow]" if link.tags else ""
        con.print(f"  [cyan]{link.id}[/cyan] [blue]{link.url}[/blue]{cred_hint}{tags_hint}")
        if link.title:
            con.print(f"       {link.title}")
        if link.notes:
            con.print(f"       [dim]{link.notes[:80]}[/dim]")


# ─────────────────────────── show ────────────────────────────────────────────


@links_app.command("show")
def show_link(
    link_id: str = typer.Argument(..., help="Link ID"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Show full details for a bookmark."""
    db = _db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found")
        raise typer.Exit(1)

    if json_output:
        _rprint(json.dumps(link.to_dict(), default=str))
        return

    con = _console()
    con.print(f"\n[bold cyan]Link: {link.id}[/bold cyan]")
    con.print(f"URL:        [blue]{link.url}[/blue]")
    con.print(f"Title:      {link.title or '—'}")
    con.print(f"Notes:      {link.notes or '—'}")
    con.print(f"Tags:       {', '.join(link.tags) or '—'}")
    con.print(
        f"Credential: {'[green]' + link.vault_cred_id + '[/green]' if link.vault_cred_id else '—'}"
    )
    con.print(f"Visits:     {link.visit_count}")
    con.print(f"Last:       {link.last_visited or '—'}")
    con.print(f"Created:    {link.created_at}")


# ─────────────────────────── open ────────────────────────────────────────────


@links_app.command("open")
def open_link(
    link_id: str = typer.Argument(..., help="Link ID"),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Browser profile to use"),
    headless: bool = typer.Option(False, "--headless", help="Open in headless mode"),
):
    """
    Open a bookmark in the browser.

    If the bookmark has a vault credential attached, queues an auto-login
    workflow via the NAVIG browser orchestrator.
    """
    import asyncio

    db = _db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found.")
        raise typer.Exit(1)

    db.record_visit(link_id)

    if link.vault_cred_id:
        _ch.info(f"Opening [blue]{link.url}[/blue] with auto-login (cred: {link.vault_cred_id})")
        try:
            from navig.integrations.browser_orchestrator import run_browser_task

            task_spec = {
                "intent": "open_link",
                "target": {"url": link.url},
                "routing": {"profile": profile or "default"},
                "steps": [
                    {"goto": {"url": link.url}},
                    {
                        "vault_fill": {
                            "credential_id": link.vault_cred_id,
                            "username_selector": "input[name='email'],input[name='username'],input[name='login'],#email,#username",
                            "password_selector": "input[type='password'],#password",
                            "submit_selector": "button[type='submit'],input[type='submit']",
                        }
                    },
                    {"wait": {"kind": "dom_ready"}},
                ],
            }
            asyncio.run(run_browser_task(task_spec))
        except Exception as exc:
            _ch.warning(f"Auto-login failed ({exc}). Opening without credentials.")
            import webbrowser

            webbrowser.open(link.url)
    else:
        _ch.info(f"Opening [blue]{link.url}[/blue]")
        import webbrowser

        webbrowser.open(link.url)


@links_app.command("edit")
def edit_link(
    link_id: str = typer.Argument(..., help="Link ID"),
    title: str | None = typer.Option(None, "--title", "-t"),
    notes: str | None = typer.Option(None, "--notes", "-n"),
    tags: str | None = typer.Option(
        None, "--tags", "-T", help="Comma-separated tags (replaces existing)"
    ),
    cred: str | None = typer.Option(None, "--cred", "-c", help="Vault credential ID"),
):
    """Edit link metadata."""
    db = _db()
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    if db.update(link_id, title=title, notes=notes, tags=tag_list, vault_cred_id=cred):
        _ch.success(f"Link {link_id} updated.")
    else:
        _ch.error(f"Link {link_id} not found.")
        raise typer.Exit(1)


# ─────────────────────────── tag ─────────────────────────────────────────────


@links_app.command("tag")
def tag_link(
    link_id: str = typer.Argument(..., help="Link ID"),
    tag: str = typer.Argument(..., help="Tag to add"),
):
    """Add a tag to a link."""
    db = _db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found.")
        raise typer.Exit(1)
    new_tags = list({*link.tags, tag})
    db.update(link_id, tags=new_tags)
    _ch.success(f"Tag '{tag}' added to link {link_id}.")


# ─────────────────────────── delete ──────────────────────────────────────────


@links_app.command("delete")
def delete_link(
    link_id: str = typer.Argument(..., help="Link ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a bookmark permanently."""
    db = _db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found.")
        raise typer.Exit(1)
    if not force:
        if not _ch.confirm_action(f"Delete bookmark for {link.url}?"):
            raise typer.Abort()
    if db.delete(link_id):
        _ch.success(f"Link {link_id} deleted.")


# ─────────────────────────── import ──────────────────────────────────────────


@links_app.command("import")
def import_links(
    file: str = typer.Argument(..., help="Path to JSON file (array of {url, title, notes, tags})"),
    cred: str | None = typer.Option(
        None, "--cred", "-c", help="Apply this vault cred to all imported links"
    ),
):
    """Bulk import bookmarks from a JSON file."""
    import pathlib

    db = _db()
    path = pathlib.Path(file)
    if not path.exists():
        _ch.error(f"File not found: {file}")
        raise typer.Exit(1)

    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)

    added = 0
    skipped = 0
    for item in items:
        url = item.get("url")
        if not url:
            continue
        if db.get_by_url(url):
            skipped += 1
            continue
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        db.add(
            url,
            title=item.get("title"),
            notes=item.get("notes"),
            tags=tags,
            vault_cred_id=cred,
        )
        added += 1

    _ch.success(f"Import complete: {added} added, {skipped} duplicates skipped.")
