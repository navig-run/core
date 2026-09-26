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

from navig.console_helper import get_console
from navig.lazy_loader import lazy_import

_ch = lazy_import("navig.console_helper")
_links_db_mod = lazy_import("navig.memory.links_db")

links_app = typer.Typer(name="links", help="Manage browser bookmarks with vault auto-login")


def _Table(*args, **kwargs):
    from rich.table import Table

    return Table(*args, **kwargs)


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
    db = _links_db_mod.get_links_db()

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
    db = _links_db_mod.get_links_db()

    if tag:
        links = db.list_by_tag(tag)
    elif cred:
        links = db.list_with_vault_cred(cred)
    else:
        links = db.list_all(limit=limit)

    if json_output:
        _rprint(json.dumps([lnk.to_dict() for lnk in links], default=str))
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

    get_console().print(table)


# ─────────────────────────── search ──────────────────────────────────────────


@links_app.command("search")
def search_links(
    query: str = typer.Argument(..., help="Search query (supports FTS5 syntax)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Full-text search bookmarks by URL, title, notes, or tags."""
    db = _links_db_mod.get_links_db()
    links = db.search(query, limit=limit)

    if json_output:
        _rprint(json.dumps([lnk.to_dict() for lnk in links], default=str))
        return

    if not links:
        _ch.warning(f"No bookmarks matching '{query}'.")
        return

    con = get_console()
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
    db = _links_db_mod.get_links_db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found")
        raise typer.Exit(1)

    if json_output:
        _rprint(json.dumps(link.to_dict(), default=str))
        return

    con = get_console()
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
    profile: str | None = typer.Option(
        None, "--profile", "-p",
        help="Named persistent browser profile (keeps the logged-in session); omit for a "
             "throwaway one.",
    ),
    headless: bool = typer.Option(
        False, "--headless",
        help="Log in without showing a window, then close it. Useful to warm a --profile.",
    ),
):
    """
    Open a bookmark in the browser.

    If the bookmark has a vault credential attached, a NAVIG-launched browser opens the
    page and fills the login from the vault (the credential is matched by the page's
    domain, the same way ``navig cdp login`` does). Without a credential the link opens
    in your default browser.
    """
    import asyncio

    db = _links_db_mod.get_links_db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found.")
        raise typer.Exit(1)

    db.record_visit(link_id)

    if not link.vault_cred_id:
        if headless:
            # Nothing to log into and nothing to look at: say so rather than open a
            # window the flag promised not to.
            _ch.warning("No credential is attached to this link, so --headless has nothing to do.")
            raise typer.Exit(2)
        _ch.info(f"Opening [blue]{link.url}[/blue]")
        import webbrowser

        webbrowser.open(link.url)
        return

    # Auto-login. This used to POST a task to /api/v1/browser/task -- a route nothing in the
    # repository serves; the module it lived in describes itself as a bridge to a "Go browser
    # executor" this Python-only core never had. Every credentialed `links open` since the
    # command existed hit that dead route, caught the error, and fell to a plain open. The
    # working stack is the CDP one behind `navig cdp new` + `navig cdp login`.
    _ch.info(f"Opening [blue]{link.url}[/blue] with auto-login (cred: {link.vault_cred_id})")
    from navig.browser import cdp_actions

    # context="human": this is an operator command, so the visibility default is a window.
    launched = cdp_actions.new(app="chrome", profile=profile, headless=headless, context="human")
    if not launched.get("ok"):
        _ch.warning(
            f"Could not start a browser for auto-login ({launched.get('error', 'unknown')}). "
            f"Opening without credentials."
        )
        import webbrowser

        webbrowser.open(link.url)
        return

    port = launched["port"]
    try:
        # login() navigates to open_url and matches the vaulted credential by that page's
        # registrable domain; the password stays server-side and is never returned.
        result = asyncio.run(cdp_actions.login(port, open_url=link.url))
    except Exception as exc:  # noqa: BLE001 -- the page is open either way; report, don't crash
        result = {"status": "error", "error": str(exc)}

    status = result.get("status") or ("ok" if result.get("ok") else "error")
    if status in ("ok", "submitted", "filled"):
        _ch.success(f"Logged in on port {port}.")
    elif status == "no_credential":
        _ch.warning(
            f"No vault login found for {link.url}. Add one with 'navig vault login add', "
            f"or fix the link's --cred."
        )
    else:
        _ch.warning(f"Auto-login did not complete ({status}: {result.get('error', '')}).")

    if headless:
        # A headless browser is invisible; leaving it running is a leak. With --profile the
        # logged-in session is already persisted on disk, which is the point of the flag.
        cdp_actions.stop(port=port)
        _ch.info("Headless session closed" + (f"; profile '{profile}' keeps the login." if profile else "."))
    else:
        _ch.info(f"Browser is open on port {port} — close it when you are done, or 'navig cdp stop --port {port}'.")



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
    db = _links_db_mod.get_links_db()
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
    db = _links_db_mod.get_links_db()
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
    db = _links_db_mod.get_links_db()
    link = db.get(link_id)
    if not link:
        _ch.error(f"Link {link_id} not found.")
        raise typer.Exit(1)
    if not force:
        if not _ch.confirm_action(f"Delete bookmark for {link.url}?"):
            raise typer.Abort()
    # A success-only branch makes a FAILED delete print nothing at all and exit 0 —
    # the bookmark is still there and the command looks like it worked. Inconsistent
    # with the rest of this very function, which exits 1 on not-found and aborts on a
    # declined confirm.
    if not db.delete(link_id):
        _ch.error(f"Failed to delete link {link_id}.")
        raise typer.Exit(1)
    _ch.success(f"Link {link_id} deleted.")


# ─────────────────────────── import ──────────────────────────────────────────


@links_app.command("import")
def import_links(
    file: str = typer.Argument(..., help="Path to JSON file (array of {url, title, notes, tags})"),
    source: str = typer.Option(
        "auto",
        "--source",
        help="Source format: auto|json|chrome|edge|firefox|safari",
    ),
    cred: str | None = typer.Option(
        None, "--cred", "-c", help="Apply this vault cred to all imported links"
    ),
):
    """Bulk import bookmarks from legacy JSON or native browser bookmark files."""
    import pathlib

    db = _links_db_mod.get_links_db()
    path = pathlib.Path(file)
    if not path.exists():
        _ch.error(f"File not found: {file}")
        raise typer.Exit(1)

    added = 0
    skipped = 0
    normalized_items: list[dict] = []

    if source in {"auto", "chrome", "edge", "firefox", "safari"}:
        try:
            from navig.importers.core import UniversalImporter

            engine = UniversalImporter()
            imported = []
            if source == "auto":
                inferred, imported = engine.run_path(str(path))
                if inferred:
                    _ch.info(f"Detected source: {inferred}")
            else:
                imported = engine.run_one(source, path=str(path))

            normalized_items = [
                {
                    "url": item.value,
                    "title": item.label,
                    "notes": f"Imported folder: {(item.meta or {}).get('folder', '')}".rstrip(),
                    "tags": ["imported", item.source],
                }
                for item in imported
                if item.type == "bookmark"
            ]
            # A source that could not be READ yields no items and would drop into the JSON
            # fallback below, which then fails on a *different* error (a places.sqlite is
            # not JSON) — burying the real cause. Name it while we still know it.
            if not normalized_items and engine.errors:
                for src, reason in sorted(engine.errors.items()):
                    _ch.error(f"{src}: could not read source — {reason}")
        except Exception as exc:
            _ch.warning(f"Universal importer unavailable ({exc}); falling back to JSON parser")

    if not normalized_items:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
        for item in items:
            url = item.get("url")
            if not url:
                continue
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            normalized_items.append(
                {
                    "url": url,
                    "title": item.get("title"),
                    "notes": item.get("notes"),
                    "tags": tags,
                }
            )

    repaired = 0
    for item in normalized_items:
        url = item.get("url")
        if not url:
            continue
        existing = db.get_by_url(url)
        if existing is not None:
            # Same self-repair as `navig import` — a bookmark stored by the pre-fix Safari
            # parser carries its own URL as its title, and dedupe-on-url would otherwise
            # keep it broken forever. Only that exact signature is touched.
            new_title = str(item.get("title") or "").strip()
            if new_title and new_title != url and existing.title == url:
                db.update(existing.id, title=new_title)
                repaired += 1
            else:
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

    _summary = f"Import complete: {added} added, {skipped} duplicates skipped"
    if repaired:
        _summary += f", {repaired} title(s) repaired"
    _ch.success(f"{_summary}.")
