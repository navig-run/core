"""navig prompts — the prompt library (Prompt Manager) + file-based prompts.

Two sources, one command group:

* **The library** (``navig.store.prompt_library``) holds the operator's own prompts:
  import a folder, add, edit, favourite, search, fill placeholders, copy. NAVIG OS
  (Knowledge → Prompts) and the Telegram ``/prompt`` command read the same database.
* **File prompts** are discovered across every root (user store · packages · active
  workshop · Claude commands) via ``navig.prompts.registry``. ``show``, ``edit`` and
  ``remove`` still work on the user store at ``~/.navig/store/prompts/`` when the
  name is not a library prompt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from navig.console_helper import get_console
from navig.platform.paths import config_dir

prompts_app = typer.Typer(help="Your prompt library: import, find, fill, copy and manage prompts", no_args_is_help=True)
console = get_console()

# Test seam — when ``None`` (the normal state), the resolver below evaluates
# at CALL time so NAVIG_CONFIG_DIR isolation set after import still applies
# (see navig/vault/migrate.py:_legacy_db_path).
_PROMPTS_DIR: Path | None = None


def _prompts_dir() -> Path:
    return _PROMPTS_DIR if _PROMPTS_DIR is not None else config_dir() / "store" / "prompts"


def _store():
    from navig.store.prompt_library import get_prompt_store

    return get_prompt_store()


def _library_table(records, *, title: str | None = None):
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2), title=title)
    table.add_column("", no_wrap=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", no_wrap=True)
    table.add_column("When to use", style="dim")
    table.add_column("Vars", justify="right", no_wrap=True)
    for r in records:
        star = "[yellow]★[/yellow]" if r.favorite else ""
        when = r.use_when if len(r.use_when) <= 90 else r.use_when[:89] + "…"
        table.add_row(star, r.id, r.title[:48], when, str(len(r.variables)) if r.variables else "[dim]—[/dim]")
    return table


def _resolve_one(name: str):
    """One library record for ``name``, or None. Several matches → print and exit 1."""
    store = _store()
    hits = store.resolve(name, limit=10)
    if not hits:
        return None
    if len(hits) == 1 or hits[0].id == name:
        return hits[0]
    console.print(f"[yellow]{len(hits)} prompts match[/yellow] {name!r}. Use an exact id:")
    console.print(_library_table(hits))
    raise typer.Exit(1)


# ── list / search / categories ──────────────────────────────────────────────


@prompts_app.command("list")
def prompts_list(
    all_scopes: bool = typer.Option(
        False, "--all", "-a", help="Include builtin (internal LLM) prompts too."
    ),
    domain: str = typer.Option("", "--domain", "-d", help="Filter the library by domain (dev, human…)."),
    category: str = typer.Option("", "--category", "-c", help="Filter the library by category."),
    collection: str = typer.Option("", "--collection", help="Only one workflow's steps, in order."),
    tag: str = typer.Option("", "--tag", "-t", help="Filter the library by tag."),
    fav: bool = typer.Option(False, "--fav", "-f", help="Only favourites."),
    sort: str = typer.Option("title", "--sort", help="title · recent · used · updated"),
    limit: int = typer.Option(200, "--limit", "-n", help="Max library rows."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """List your prompt library, then file prompts from every root."""
    from collections import defaultdict

    from navig.prompts.registry import load_all_prompts

    store = _store()
    lib = store.list(
        domain=domain, category=category, collection=collection, tag=tag,
        favorite=fav, sort=sort, limit=limit,
    )
    filtered = any((domain, category, collection, tag, fav))

    if as_json:
        print(json.dumps([r.to_dict(with_body=False) for r in lib], ensure_ascii=False, indent=2))
        return

    if lib:
        total = store.count()
        console.print(_library_table(lib, title=f"Prompt library · {len(lib)} of {total}"))
        console.print(
            "[dim]Copy one with[/dim] navig prompts use <id> [dim]· find with[/dim] "
            "navig prompts search <words> [dim]· filters:[/dim] navig prompts categories"
        )
        if filtered:
            return
    elif filtered:
        console.print("[dim]No library prompts match those filters.[/dim] See navig prompts categories")
        raise typer.Exit(1)

    discovered = load_all_prompts()
    prompts = discovered if all_scopes else [p for p in discovered if p.scope != "builtin"]
    if not prompts:
        if lib:
            return
        # "No prompts found" is only true when nothing was FOUND. NAVIG ships 34 builtin
        # prompts, so on a stock install the default view is empty purely because they
        # were filtered — and reporting that as "none" tells the operator navig has no
        # prompts at all, with `--all` (the one thing that would show them) unmentioned.
        # Same rule as `navig doctor`: never report an absence you did not verify.
        hidden = len(discovered) - len(prompts)
        if hidden:
            console.print(
                f"[dim]No user, space or package prompts — {hidden} builtin "
                f"(internal LLM) prompt(s) hidden. See them with[/dim] "
                "navig prompts list --all"
            )
        else:
            console.print("[dim]No prompts found.[/dim]")
        console.print(
            "[dim]Add one with[/dim] navig prompts new <title> "
            "[dim]or import a folder with[/dim] navig prompts import <folder>"
        )
        return

    by_scope: dict[str, list] = defaultdict(list)
    for p in prompts:
        by_scope[p.scope].append(p)
    if lib:
        console.print()
        console.print("[bold]File prompts[/bold]")
    for scope in ("user", "space", "package", "claude", "builtin"):
        items = by_scope.get(scope)
        if not items:
            continue
        console.print(f"[bold]{scope}[/bold]")
        for p in sorted(items, key=lambda x: x.id):
            desc = f"  [dim]{p.description}[/dim]" if p.description else ""
            console.print(f"  [cyan]{p.id}[/cyan]{desc}")


@prompts_app.command("search")
def prompts_search(
    words: list[str] = typer.Argument(..., help="Words to look for (title, when-to-use, body, tags)."),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """Full-text search of the prompt library."""
    q = " ".join(words)
    hits = _store().list(q=q, limit=limit)
    if as_json:
        print(json.dumps([r.to_dict(with_body=False) for r in hits], ensure_ascii=False, indent=2))
        return
    if not hits:
        console.print(f"[yellow]No prompt matches[/yellow] {q!r}. Try fewer words.")
        raise typer.Exit(1)
    console.print(_library_table(hits, title=f"{len(hits)} match {q!r}"))


@prompts_app.command("categories")
def prompts_categories(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")):
    """Domains, categories, workflows and tags in the library, with counts."""
    from navig.console_helper import Table

    f = _store().facets()
    if as_json:
        print(json.dumps(f, ensure_ascii=False, indent=2))
        return
    if not f["total"]:
        console.print("[dim]The library is empty.[/dim] Import a folder with navig prompts import <folder>")
        return
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Domain", style="bold", no_wrap=True)
    table.add_column("Prompts", justify="right", no_wrap=True)
    table.add_column("Categories")
    for name, d in sorted(f["domains"].items(), key=lambda kv: -kv[1]["count"]):
        cats = " · ".join(f"{c} [dim]{n}[/dim]" for c, n in sorted(d["categories"].items()))
        table.add_row(name or "[dim](none)[/dim]", str(d["count"]), cats)
    console.print(table)
    if f["collections"]:
        cols = " · ".join(f"{c['name']} [dim]{c['count']}[/dim]" for c in f["collections"])
        console.print(f"[bold]Workflows[/bold]  {cols}")
    console.print(
        f"[dim]{f['total']} prompts · {f['favorites']} favourites · filter with[/dim] "
        "navig prompts list --domain <d> --category <c>"
    )


# ── show / use ──────────────────────────────────────────────────────────────


@prompts_app.command("show")
def prompts_show(
    name: str = typer.Argument(..., help="Prompt id or name"),
    raw: bool = typer.Option(False, "--raw", help="Print only the prompt body (for piping)."),
):
    """Show a prompt from the library (or a saved prompt file)."""
    rec = _resolve_one(name)
    if rec is not None:
        if raw:
            sys.stdout.write(rec.body.rstrip() + "\n")
            return
        where = " · ".join(x for x in (rec.domain, rec.category, rec.collection) if x)
        console.print(f"[bold]{rec.title}[/bold]  [dim]{rec.id}[/dim]")
        if where:
            console.print(f"[dim]{where}{f' · step {rec.step}' if rec.step else ''}[/dim]")
        if rec.use_when:
            console.print(f"[italic]{rec.use_when}[/italic]")
        if rec.variables:
            console.print("[yellow]Fill in:[/yellow] " + ", ".join(rec.variables))
        console.print()
        console.print(rec.body, markup=False, highlight=False)
        return

    _prompts_dir().mkdir(parents=True, exist_ok=True)
    for ext in (".txt", ".md", ""):
        target = _prompts_dir() / (name + ext)
        if target.exists():
            console.print(target.read_text(encoding="utf-8"))
            return
    console.print(f"[red]Prompt not found:[/red] {name}")
    raise typer.Exit(1)


def _copy_to_clipboard(text: str) -> bool:
    import shutil
    import subprocess

    try:
        if sys.platform == "win32":
            # clip.exe reads UTF-16LE (with BOM) losslessly; a plain encode mangles —·→ and emoji.
            subprocess.run(["clip"], input=("﻿" + text).encode("utf-16-le"), check=True, timeout=10)
            return True
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=10)
            return True
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=10)
                return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


@prompts_app.command("use")
def prompts_use(
    name: str = typer.Argument(..., help="Prompt id or name"),
    var: list[str] = typer.Option([], "--var", "-v", help="Fill a placeholder: --var TOPIC=cats (repeatable)."),
    ask: bool = typer.Option(False, "--ask", help="Prompt for every placeholder not given with --var."),
    copy: bool = typer.Option(True, "--copy/--no-copy", help="Copy the result to the clipboard."),
):
    """Fill a prompt's placeholders, copy it, and print it."""
    from navig.prompts.importer import fill_variables

    rec = _resolve_one(name)
    if rec is None:
        console.print(f"[red]No library prompt matches[/red] {name!r}. See navig prompts search <words>")
        raise typer.Exit(1)
    values: dict[str, str] = {}
    for item in var:
        if "=" not in item:
            console.print(f"[red]--var needs KEY=VALUE, got[/red] {item!r}")
            raise typer.Exit(2)
        k, v = item.split("=", 1)
        values[k] = v
    if ask:
        for token in rec.variables:
            short = token.strip("[]{}‹› ").split("—")[0].strip()
            if any(k.strip("[]{}‹› ").upper() in (short.upper(), token.upper()) for k in values):
                continue
            values[token] = typer.prompt(short, default="", show_default=False) or token
    text = fill_variables(rec.body, values)
    _store().record_use(rec.id)
    sys.stdout.write(text.rstrip() + "\n")
    if copy:
        if _copy_to_clipboard(text):
            console.print(f"[green]✓ Copied[/green] {rec.title}", style=None, highlight=False)
        else:
            console.print("[yellow]Could not reach the clipboard — the prompt is printed above.[/yellow]")
    left = [v for v in rec.variables if v in text]
    if left:
        console.print("[dim]Still to fill:[/dim] " + ", ".join(left))


# ── add / edit / favourite / remove ─────────────────────────────────────────


@prompts_app.command("new")
def prompts_new(
    title: str = typer.Argument(..., help="Prompt title"),
    body: str = typer.Option("", "--body", "-b", help="Prompt text (else --from-file, stdin, or your editor)."),
    from_file: Path = typer.Option(None, "--from-file", help="Read the prompt text from a file."),
    domain: str = typer.Option("", "--domain", "-d", help="Domain, e.g. dev, human, business."),
    category: str = typer.Option("", "--category", "-c", help="Category inside the domain."),
    use_when: str = typer.Option("", "--use-when", "-w", help="One line: when to launch this prompt."),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags."),
):
    """Add a prompt to the library."""
    text = body
    if from_file is not None:
        from navig.platform.paths import resolve_user_path

        text = resolve_user_path(str(from_file)).read_text(encoding="utf-8")
    elif not text and not sys.stdin.isatty():
        text = sys.stdin.read()
    elif not text:
        text = _edit_in_editor("") or ""
    if not text.strip():
        console.print("[red]Empty prompt — nothing saved.[/red]")
        raise typer.Exit(1)
    try:
        rec = _store().create(
            title=title, body=text, domain=domain, category=category, use_when=use_when, tags=tags.split(","),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓ Saved[/green] {rec.title} [dim]→ {rec.id}[/dim]")


def _edit_in_editor(initial: str) -> str | None:
    import os
    import subprocess
    import tempfile

    editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "nano")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(initial)
        tmp = Path(fh.name)
    try:
        result = subprocess.run([*editor.split(), str(tmp)], check=False)
        if result.returncode != 0:
            return None
        return tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)


@prompts_app.command("edit")
def prompts_edit(name: str = typer.Argument(..., help="Prompt id or name")):
    """Edit a library prompt's text in your editor (or open a prompt file)."""
    import os

    rec = _resolve_one(name)
    if rec is not None:
        new_body = _edit_in_editor(rec.body)
        if new_body is None:
            console.print("[yellow]Editor exited with an error — nothing changed.[/yellow]")
            raise typer.Exit(1)
        if new_body.strip() == rec.body.strip():
            console.print("[dim]No changes.[/dim]")
            return
        if not new_body.strip():
            console.print("[red]Refusing to save an empty prompt.[/red] Use navig prompts remove to delete it.")
            raise typer.Exit(1)
        _store().update(rec.id, {"body": new_body})
        console.print(f"[green]✓ Updated[/green] {rec.title}")
        return

    _prompts_dir().mkdir(parents=True, exist_ok=True)
    target = _prompts_dir() / f"{name}.md"
    editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "nano")
    os.execlp(editor, editor, str(target))


@prompts_app.command("fav")
def prompts_fav(
    name: str = typer.Argument(..., help="Prompt id or name"),
    off: bool = typer.Option(False, "--off", help="Remove from favourites."),
):
    """Star a prompt (favourites show first in NAVIG OS and Telegram /prompt)."""
    rec = _resolve_one(name)
    if rec is None:
        console.print(f"[red]No library prompt matches[/red] {name!r}")
        raise typer.Exit(1)
    rec = _store().set_favorite(rec.id, not off)
    console.print(("[yellow]★ Favourite[/yellow] " if rec.favorite else "[dim]Unstarred[/dim] ") + rec.title)


@prompts_app.command("remove")
def prompts_remove(
    name: str = typer.Argument(..., help="Prompt id or name"),
    purge: bool = typer.Option(False, "--purge", help="Delete for good instead of archiving."),
):
    """Delete a prompt (library prompts are archived unless --purge)."""
    for ext in (".txt", ".md", ""):
        target = _prompts_dir() / (name + ext)
        if target.exists():
            target.unlink()
            console.print(f"[green]Deleted:[/green] {target.name}")
            return
    store = _store()
    rec = store.get(name)
    if rec is not None:
        store.delete(rec.id, purge=purge)
        verb = "Deleted" if purge else "Archived"
        console.print(f"[green]{verb}:[/green] {rec.title} [dim]({rec.id})[/dim]")
        if not purge:
            console.print("[dim]Undo with[/dim] navig prompts restore " + rec.id)
        return
    console.print(f"[yellow]Not found:[/yellow] {name}")


@prompts_app.command("restore")
def prompts_restore(name: str = typer.Argument(..., help="Exact id of an archived prompt")):
    """Bring back an archived library prompt."""
    store = _store()
    if store.get(name) is None:
        console.print(f"[red]No library prompt with id[/red] {name!r}")
        raise typer.Exit(1)
    rec = store.update(name, {"archived": False})
    console.print(f"[green]✓ Restored[/green] {rec.title}")


# ── import / export ─────────────────────────────────────────────────────────


@prompts_app.command("import")
def prompts_import(
    folder: Path = typer.Argument(..., help="Folder of .md/.txt prompts (domain/category/name.md)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change; write nothing."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report."),
):
    """Import (or re-sync) a folder of prompts. Safe to run again — your edits are kept."""
    from navig.platform.paths import resolve_user_path
    from navig.prompts.importer import import_library

    root = resolve_user_path(str(folder))
    try:
        report = import_library(root, dry_run=dry_run)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    d = report.to_dict()
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    from navig.console_helper import Table

    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    table.add_row("[green]● added[/green]", str(d["added"]))
    table.add_row("[cyan]● updated[/cyan]", str(d["updated"]))
    table.add_row("[dim]○ unchanged[/dim]", str(d["unchanged"]))
    table.add_row("[yellow]● kept your edit[/yellow]", str(len(d["conflicts"])))
    table.add_row("[dim]○ skipped[/dim]", str(len(d["skipped"])))
    console.print(f"[bold]{'Would import' if dry_run else 'Imported'}[/bold] {root}")
    console.print(table)
    for item in d["skipped"][:10]:
        console.print(f"  [dim]skipped {item['path']} — {item['reason']}[/dim]")
    if d["conflicts"]:
        console.print("[dim]Edited in navig, so the folder version was not applied:[/dim] " + ", ".join(d["conflicts"][:10]))
    if dry_run:
        console.print("[dim]Run again without --dry-run to apply.[/dim]")
    else:
        console.print("[dim]Browse with[/dim] navig prompts categories [dim]· NAVIG OS → Knowledge → Prompts · Telegram /prompt[/dim]")


@prompts_app.command("backup")
def prompts_backup(
    dest: Path = typer.Argument(..., help="Folder to write <id>.md files into."),
    include_archived: bool = typer.Option(False, "--archived", help="Include archived prompts."),
):
    """Write the whole library out as Markdown files (re-importable)."""
    from navig.platform.paths import resolve_user_path
    from navig.prompts.importer import export_library

    out = resolve_user_path(str(dest))
    n = export_library(out, include_archived=include_archived)
    console.print(f"[green]✓ Wrote {n} prompts[/green] → {out}")


@prompts_app.command("export")
def prompts_export(
    name: str = typer.Argument(..., help="Prompt id (see `navig prompts list`)."),
    dest: Path = typer.Option(
        Path(".claude/commands"), "--dest", "-o",
        help="Output dir for the Claude slash command (.claude/commands).",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing export."),
):
    """Export a prompt as a Claude slash command (``.claude/commands/<id>.md``)."""
    from navig.prompts.registry import export_prompt, prompts_by_id

    if name not in prompts_by_id():
        rec = _store().get(name)
        if rec is not None:
            from navig.platform.paths import resolve_user_path

            target = resolve_user_path(str(dest)) / f"{rec.id.rsplit('/', 1)[-1]}.md"
            if target.exists() and not force:
                console.print(f"[red]{target} exists — use --force to overwrite.[/red]")
                raise typer.Exit(1)
            target.parent.mkdir(parents=True, exist_ok=True)
            head = f"---\ndescription: {json.dumps(rec.use_when or rec.title, ensure_ascii=False)}\n---\n\n"
            target.write_text(head + rec.body.rstrip() + "\n", encoding="utf-8")
            console.print(f"[green]Exported[/green] {name} → {target}")
            return
    try:
        out = export_prompt(name, dest, force=force)
    except (ValueError, FileExistsError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Exported[/green] {name} → {out}")
