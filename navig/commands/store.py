"""
``navig store`` — Unified store diagnostics, maintenance, and backup.

Subcommands:
    status      Show status of all local SQLite databases.
    maintenance Run PRAGMA optimize, WAL checkpoint, integrity check.
    backup      Backup databases to a destination directory.
    migrate     Run one-time data migrations (legacy → new stores).
    cleanup     Remove deprecated / migrated database files.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from navig.console_helper import get_console
from navig.platform.paths import config_dir

console = get_console()

store_app = typer.Typer(
    name="store",
    help="Local SQLite store management — status, maintenance, backup, migrate.",
    no_args_is_help=True,
)


def _navig_dir() -> Path:
    """Return the active NAVIG data directory."""
    return config_dir()


# ── Status ────────────────────────────────────────────────────


@store_app.command("status")
def store_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show health status of all local SQLite stores."""
    navig = _navig_dir()
    db_files = sorted(navig.rglob("*.db"))
    if not db_files:
        console.print("[yellow]No SQLite databases found in ~/.navig/[/yellow]")
        raise typer.Exit()

    rows = []
    for db_path in db_files:
        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            size = db_path.stat().st_size
            version_row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            version = version_row[0] if version_row else "—"
            conn.close()
        except Exception as exc:
            journal = "?"
            integrity = str(exc)[:40]
            size = db_path.stat().st_size if db_path.exists() else 0
            version = "?"

        rows.append(
            {
                "path": str(db_path.relative_to(navig)),
                "size_kb": round(size / 1024, 1),
                "journal": journal,
                "version": str(version),
                "integrity": integrity,
            }
        )

    if json_output:
        import json

        console.print_json(json.dumps(rows, indent=2))
    else:
        table = Table(title="NAVIG Local Stores", show_lines=True)
        table.add_column("Database", style="cyan")
        table.add_column("Size", justify="right")
        table.add_column("Journal", justify="center")
        table.add_column("Version", justify="center")
        table.add_column("Integrity", style="green")

        for r in rows:
            integrity_style = "green" if r["integrity"] == "ok" else "red"
            table.add_row(
                r["path"],
                f"{r['size_kb']} KB",
                r["journal"],
                r["version"],
                f"[{integrity_style}]{r['integrity']}[/{integrity_style}]",
            )

        console.print(table)
        total_kb = sum(r["size_kb"] for r in rows)
        console.print(f"  [dim]{len(rows)} databases · {total_kb:.0f} KB total[/dim]")


# ── Maintenance ───────────────────────────────────────────────


@store_app.command("maintenance")
def store_maintenance(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Run maintenance on all managed stores (optimize, checkpoint, ANALYZE)."""
    from navig.store.audit import get_audit_store
    from navig.store.runtime import get_runtime_store

    results = {}

    stores = {
        "audit.db": get_audit_store,
        "runtime.db": get_runtime_store,
    }

    # Also try ConversationStore and MatrixStore if their DBs exist
    navig = _navig_dir()
    if (navig / "memory.db").exists():
        try:
            from navig.memory.conversation import ConversationStore

            stores["memory.db"] = lambda: ConversationStore(navig / "memory.db")
        except ImportError:
            pass  # optional dependency not installed; feature disabled
    if (navig / "matrix.db").exists():
        try:
            from navig.comms.matrix_store import MatrixStore

            stores["matrix.db"] = lambda: MatrixStore(navig / "matrix.db")
        except ImportError:
            pass  # optional dependency not installed; feature disabled

    for name, factory in stores.items():
        try:
            store = factory()
            start = time.time()
            result = store.maintenance()
            result["duration_ms"] = round((time.time() - start) * 1000)
            results[name] = result
            store.close()
        except Exception as exc:
            results[name] = {"error": str(exc)}

    if json_output:
        import json

        console.print_json(json.dumps(results, indent=2))
    else:
        for name, r in results.items():
            if "error" in r:
                console.print(f"  [red]✗ {name}: {r['error']}[/red]")
            else:
                size_mb = round(r.get("size_bytes", 0) / 1024 / 1024, 2)
                console.print(
                    f"  [green]✓[/green] {name}  "
                    f"integrity={r.get('integrity', '?')}  "
                    f"size={size_mb}MB  "
                    f"duration={r.get('duration_ms', '?')}ms"
                )
        console.print()


# ── Backup ────────────────────────────────────────────────────


@store_app.command("backup")
def store_backup(
    dest: str = typer.Argument(..., help="Destination directory for backup files"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Backup all managed SQLite databases to a destination directory."""
    from navig.store.audit import get_audit_store
    from navig.store.runtime import get_runtime_store

    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    results = {}
    navig = _navig_dir()

    stores_to_backup = {}

    # Managed stores
    try:
        stores_to_backup["audit.db"] = get_audit_store()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    try:
        stores_to_backup["runtime.db"] = get_runtime_store()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Other stores if present
    if (navig / "memory.db").exists():
        try:
            from navig.memory.conversation import ConversationStore

            stores_to_backup["memory.db"] = ConversationStore(navig / "memory.db")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    if (navig / "matrix.db").exists():
        try:
            from navig.comms.matrix_store import MatrixStore

            stores_to_backup["matrix.db"] = MatrixStore(navig / "matrix.db")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    for name, store in stores_to_backup.items():
        try:
            backup_name = f"{name.replace('.db', '')}_{timestamp}.db"
            backup_path = dest_path / backup_name
            store.backup(backup_path)
            size = backup_path.stat().st_size
            results[name] = {"path": str(backup_path), "size_bytes": size}
            store.close()
        except Exception as exc:
            results[name] = {"error": str(exc)}

    if json_output:
        import json

        console.print_json(json.dumps(results, indent=2))
    else:
        for name, r in results.items():
            if "error" in r:
                console.print(f"  [red]✗ {name}: {r['error']}[/red]")
            else:
                size_kb = round(r["size_bytes"] / 1024, 1)
                console.print(f"  [green]✓[/green] {name} → {r['path']} ({size_kb} KB)")
        console.print(Panel(f"Backups saved to [cyan]{dest_path}[/cyan]", title="Done"))


# ── Migrate ───────────────────────────────────────────────────


@store_app.command("migrate")
def store_migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changes"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Run pending data migrations (legacy bot_data.db, daily_log.db → runtime.db)."""
    navig = _navig_dir()
    results = {}

    # 1. RuntimeStore auto-migration handles bot_data.db + daily_log.db
    legacy_files = [
        navig / "bot" / "bot_data.db",
        navig / "daily_log.db",
    ]
    pending = [f for f in legacy_files if f.exists() and not f.with_suffix(".db.migrated").exists()]

    if not pending:
        msg = "No pending migrations — all legacy databases have been migrated."
        if json_output:
            console.print_json(f'{{"status": "clean", "message": "{msg}"}}')
        else:
            console.print(f"  [green]✓[/green] {msg}")
        raise typer.Exit()

    for f in pending:
        results[str(f.relative_to(navig))] = "pending"

    if dry_run:
        if json_output:
            import json

            console.print_json(json.dumps({"dry_run": True, "pending": results}))
        else:
            console.print("[yellow]Dry run — would migrate:[/yellow]")
            for name in results:
                console.print(f"  → {name}")
        raise typer.Exit()

    # Trigger migration by opening RuntimeStore (auto-migrates on init)
    from navig.store.runtime import RuntimeStore

    store = RuntimeStore(navig / "runtime.db")
    store.close()

    # Check results
    for f in pending:
        migrated = f.with_suffix(".db.migrated").exists()
        rel = str(f.relative_to(navig))
        results[rel] = "migrated" if migrated else "failed"

    if json_output:
        import json

        console.print_json(json.dumps(results))
    else:
        for name, status in results.items():
            if status == "migrated":
                console.print(f"  [green]✓[/green] {name} → runtime.db")
            else:
                console.print(f"  [red]✗[/red] {name} — migration failed")

    # 2. Vector embedding migration (if sqlite-vec available)
    try:
        from navig.memory.storage import MemoryStorage

        index_db = navig / "memory" / "index.db"
        if index_db.exists():
            ms = MemoryStorage(index_db)
            if ms.vec_available:
                count = ms.migrate_embeddings_to_vec()
                if count > 0:
                    console.print(f"  [green]✓[/green] Migrated {count} embeddings to vec0 table")
            ms.close()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical


# ── Cleanup ───────────────────────────────────────────────────


@store_app.command("cleanup")
def store_cleanup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without deleting"),
    force: bool = typer.Option(False, "--force", "-f", help="No confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Remove deprecated .db.migrated files and empty legacy directories."""
    navig = _navig_dir()
    targets = list(navig.rglob("*.db.migrated"))

    if not targets:
        msg = "Nothing to clean up."
        if json_output:
            console.print_json(f'{{"status": "clean", "message": "{msg}"}}')
        else:
            console.print(f"  [green]✓[/green] {msg}")
        raise typer.Exit()

    if dry_run:
        if json_output:
            import json

            console.print_json(
                json.dumps({"dry_run": True, "files": [str(f) for f in targets]}, indent=2)
            )
        else:
            console.print("[yellow]Dry run — would remove:[/yellow]")
            for f in targets:
                console.print(f"  {f.relative_to(navig)}")
        raise typer.Exit()

    if not force:
        confirm = typer.confirm(f"Remove {len(targets)} migrated backup file(s)?", default=False)
        if not confirm:
            raise typer.Abort()

    removed = []
    for f in targets:
        try:
            f.unlink()
            removed.append(str(f.relative_to(navig)))
        except Exception as exc:
            console.print(f"  [red]✗ {f.name}: {exc}[/red]")

    if json_output:
        import json

        console.print_json(json.dumps({"removed": removed}))
    else:
        for name in removed:
            console.print(f"  [green]✓[/green] Removed {name}")
        console.print(f"  [dim]{len(removed)} file(s) cleaned[/dim]")
