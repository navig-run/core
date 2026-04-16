from __future__ import annotations

from pathlib import Path

import typer

from navig.console_helper import get_console
from navig.importers.core import UniversalImporter
from navig.importers.core import flatten_results as _flatten
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")
links_db_mod = lazy_import("navig.memory.links_db")

import_app = typer.Typer(help="Universal data import from external sources")


def _table() -> object:
    from rich.table import Table

    return Table(title="Import Results")


def _persist_bookmarks(results: dict[str, list]) -> tuple[int, int]:
    db = links_db_mod.get_links_db()
    added = 0
    skipped = 0

    for item in _flatten(results):
        if item.get("type") != "bookmark":
            continue
        url = str(item.get("value") or "").strip()
        if not url:
            continue
        if db.get_by_url(url):
            skipped += 1
            continue

        meta = item.get("meta") or {}
        notes = None
        folder = meta.get("folder") if isinstance(meta, dict) else None
        if folder:
            notes = f"Imported folder: {folder}"

        db.add(
            url,
            title=str(item.get("label") or ""),
            notes=notes,
            tags=["imported", str(item.get("source") or "")],
        )
        added += 1

    return added, skipped


@import_app.command("list-sources")
def list_sources() -> None:
    """List all built-in import sources."""
    engine = UniversalImporter()
    for source in engine.list_sources():
        print(source)


def _run_import(
    source: str = typer.Option("all", "--source", help="Import source name or 'all'"),
    path: str | None = typer.Option(
        None,
        "--path",
        help="Explicit source path (required to exist; not allowed with --source all)",
    ),
    output: str | None = typer.Option(None, "--output", help="Write normalized JSON output to file"),
    persist_bookmarks: bool = typer.Option(
        True,
        "--persist-bookmarks/--no-persist-bookmarks",
        help="Persist imported bookmark items into NAVIG links storage",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print normalized JSON output to stdout"),
) -> None:
    """Run universal import for one source or all sources."""
    engine = UniversalImporter()
    available_sources = set(engine.list_sources())

    if source != "all" and source not in available_sources:
        ch.error(
            f"Unknown source '{source}'. Available: {', '.join(sorted(available_sources))}"
        )
        raise typer.Exit(1)

    if source == "all" and path:
        ch.error("--path cannot be used with --source all. Choose one source or remove --path.")
        raise typer.Exit(1)

    if path and not Path(path).exists():
        ch.error(f"Import path does not exist: {path}")
        raise typer.Exit(1)

    try:
        if source == "all":
            results = engine.run_all()
        else:
            results = {source: engine.run_one(source, path=path)}
    except (ValueError, FileNotFoundError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None

    payload = engine.export_json(results)
    if output:
        from navig.core.yaml_io import atomic_write_text

        atomic_write_text(Path(output), payload)
        ch.success(f"Wrote import output: {output}")

    if persist_bookmarks:
        added, skipped = _persist_bookmarks(results)
        ch.info(f"Bookmark persistence: {added} added, {skipped} duplicates skipped")

    if json_output:
        print(payload)
        return

    rows = _flatten(results)
    if not rows:
        ch.warning("No items imported.")
        return

    try:
        table = _table()
        table.add_column("source", style="cyan")
        table.add_column("type", style="magenta")
        table.add_column("label", style="white")
        table.add_column("value", style="blue")

        for row in rows:
            table.add_row(
                str(row.get("source", "")),
                str(row.get("type", "")),
                str(row.get("label", "")),
                str(row.get("value", "")),
            )

        get_console().print(table)
    except Exception:
        for row in rows:
            print(
                f"{row.get('source', '')}\t{row.get('type', '')}\t"
                f"{row.get('label', '')}\t{row.get('value', '')}"
            )


@import_app.callback(invoke_without_command=True)
def run_import(
    ctx: typer.Context,
    source: str = typer.Option("all", "--source", help="Import source name or 'all'"),
    path: str | None = typer.Option(
        None,
        "--path",
        help="Explicit source path (required to exist; not allowed with --source all)",
    ),
    output: str | None = typer.Option(None, "--output", help="Write normalized JSON output to file"),
    persist_bookmarks: bool = typer.Option(
        True,
        "--persist-bookmarks/--no-persist-bookmarks",
        help="Persist imported bookmark items into NAVIG links storage",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print normalized JSON output to stdout"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _run_import(
        source=source,
        path=path,
        output=output,
        persist_bookmarks=persist_bookmarks,
        json_output=json_output,
    )
