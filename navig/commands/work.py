"""
Work Commands

NAVIG-wide lifecycle and stage tracker.  Tracks anything that moves through
stages: leads, clients, projects, tasks, proposals, initiatives, and more.

Every work item can be linked to a wiki page (created automatically on ``add``
if ``--no-wiki`` is not passed).

Usage:
    navig work add "Redesign homepage" --kind project --stage inbox
    navig work list
    navig work list --kind lead --stage active
    navig work show redesign-homepage
    navig work move redesign-homepage --to active
    navig work update redesign-homepage --title "Redesign main landing" --tag design
    navig work archive redesign-homepage
    navig work stages
    navig work kinds
"""

from __future__ import annotations

import json
import logging
import os as _os
import re
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import typer

from navig import console_helper as ch

_log = logging.getLogger(__name__)


def _print_json(data) -> None:
    """Emit *data* as indented JSON to stdout."""
    typer.echo(json.dumps(data, indent=2, default=str))


# ── App ─────────────────────────────────────────────────────────────────────

work_app = typer.Typer(
    name="work",
    help="Track work items (leads, projects, tasks, …) across stages.",
    no_args_is_help=True,
)

# ── Constants ────────────────────────────────────────────────────────────────

VALID_KINDS = ("lead", "client", "project", "task", "proposal", "initiative", "other")
VALID_STAGES = ("inbox", "planned", "active", "blocked", "review", "done", "archived")

_DB_NAME = "work.db"

# Stage transitions: allowed moves (permissive — any → any is allowed but
# shown as a warning when going backwards more than two hops).
_STAGE_ORDER = {s: i for i, s in enumerate(VALID_STAGES)}

# Root of NAVIG data directory. Overridable in tests via monkeypatch.
# Respects NAVIG_CONFIG_DIR env var for non-default install paths.
from navig.core.dict_utils import now_iso
from navig.platform.paths import config_dir as _config_dir

_NAVIG_ROOT: Path = _config_dir()


# ── DB helpers ───────────────────────────────────────────────────────────────


def _db_path() -> Path:
    """Return path to the work SQLite database."""
    return _NAVIG_ROOT / "store" / _DB_NAME


def _get_conn():
    """Return an Engine-managed connection to the work database.

    Creates the schema if it does not exist (idempotent).
    """
    from navig.storage.engine import Engine

    engine = Engine()
    conn = engine.connect(_db_path())
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn) -> None:
    """Create work_items and work_events tables if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS work_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            slug         TEXT NOT NULL UNIQUE,
            title        TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'task',
            stage        TEXT NOT NULL DEFAULT 'inbox',
            owner        TEXT,
            notes_path   TEXT,
            ref_type     TEXT,
            ref_id       TEXT,
            tags_json    TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            archived_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS work_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            work_item_id INTEGER NOT NULL REFERENCES work_items(id),
            event_type   TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_work_items_kind  ON work_items(kind);
        CREATE INDEX IF NOT EXISTS idx_work_items_stage ON work_items(stage);
        CREATE INDEX IF NOT EXISTS idx_work_events_item ON work_events(work_item_id);
        """
    )
    conn.commit()


def _slugify(title: str) -> str:
    """Convert a title to a URL-safe slug."""
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80]  # cap length


def _unique_slug(conn, base_slug: str) -> str:
    """Ensure the slug is unique, appending -N suffixes as needed."""
    slug = base_slug
    n = 2
    while conn.execute("SELECT 1 FROM work_items WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{n}"
        n += 1
    return slug



def _record_event(conn, work_item_id: int, event_type: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO work_events (work_item_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (work_item_id, event_type, json.dumps(payload), now_iso()),
    )


# ── Wiki integration ─────────────────────────────────────────────────────────


def _create_wiki_note(slug: str, title: str, kind: str, stage: str) -> str | None:
    """Create a wiki hub note for the work item.

    Returns the relative wiki path on success, or None if wiki is not
    initialised or the import fails.
    """
    try:
        data_root = _NAVIG_ROOT
        wiki_root = data_root / "wiki" / "hub"

        if not wiki_root.exists():
            # Wiki not initialised — soft fail
            return None

        today = datetime.now().strftime("%Y-%m-%d")
        note_path = wiki_root / f"{slug}.md"

        if note_path.exists():
            return str(note_path.relative_to(data_root / "wiki")).replace("\\", "/")

        frontmatter = textwrap.dedent(
            f"""\
            ---
            title: "{title}"
            kind: {kind}
            stage: {stage}
            work_slug: {slug}
            created: {today}
            tags: []
            ---

            # {title}

            *Work item type*: `{kind}` | *Stage*: `{stage}`

            ## Notes

            <!-- Add notes, links, and context here. -->

            ## History

            - {today}: Created
            """
        )
        note_path.write_text(frontmatter, encoding="utf-8")
        return str(note_path.relative_to(data_root / "wiki"))

    except Exception:  # noqa: BLE001 — wiki is optional, never block work ops
        return None


# ── Commands ─────────────────────────────────────────────────────────────────


@work_app.command("add")
def cmd_add(
    title: str = typer.Argument(..., help="Title of the work item."),
    kind: str = typer.Option("task", "--kind", "-k", help=f"Item kind: {', '.join(VALID_KINDS)}."),
    stage: str = typer.Option(
        "inbox", "--stage", "-s", help=f"Initial stage: {', '.join(VALID_STAGES)}."
    ),
    owner: str | None = typer.Option(None, "--owner", "-o", help="Owner name or identifier."),
    tag: list[str] = typer.Option([], "--tag", "-t", help="Tag (repeatable)."),
    no_wiki: bool = typer.Option(False, "--no-wiki", help="Do not create a linked wiki note."),
    json_out: bool = typer.Option(False, "--json", help="Output result as JSON."),
):
    """Add a new work item."""
    kind = kind.lower()
    stage = stage.lower()

    if kind not in VALID_KINDS:
        ch.error(f"Invalid kind '{kind}'. Choose from: {', '.join(VALID_KINDS)}")
        raise typer.Exit(code=1)
    if stage not in VALID_STAGES:
        ch.error(f"Invalid stage '{stage}'. Choose from: {', '.join(VALID_STAGES)}")
        raise typer.Exit(code=1)

    conn = _get_conn()
    base_slug = _slugify(title)
    slug = _unique_slug(conn, base_slug)
    now = now_iso()

    notes_path: str | None = None
    if not no_wiki:
        notes_path = _create_wiki_note(slug, title, kind, stage)

    conn.execute(
        """
        INSERT INTO work_items
            (slug, title, kind, stage, owner, notes_path, tags_json,
             metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slug,
            title,
            kind,
            stage,
            owner,
            notes_path,
            json.dumps(list(tag)),
            "{}",
            now,
            now,
        ),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM work_items WHERE slug = ?", (slug,)).fetchone()
    item_id = row["id"]
    _record_event(conn, item_id, "created", {"kind": kind, "stage": stage})
    conn.commit()

    if json_out:
        _print_json({"id": item_id, "slug": slug, "notes_path": notes_path})
    else:
        ch.success(f"Created work item [{item_id}] {slug!r}")
        if notes_path:
            ch.info(f"  Wiki note: {notes_path}")
        elif not no_wiki:
            ch.dim("  (Wiki not initialised — run `navig wiki init` to enable notes.)")


@work_app.command("list")
def cmd_list(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by kind."),
    stage: str | None = typer.Option(None, "--stage", "-s", help="Filter by stage."),
    owner: str | None = typer.Option(None, "--owner", "-o", help="Filter by owner."),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
):
    """List work items, optionally filtered."""
    conn = _get_conn()

    sql = "SELECT * FROM work_items WHERE 1=1"
    params: list = []

    if kind:
        sql += " AND kind = ?"
        params.append(kind.lower())
    if stage:
        sql += " AND stage = ?"
        params.append(stage.lower())
    if owner:
        sql += " AND owner = ?"
        params.append(owner)
    if tag:
        # tags stored as JSON array — use json_each for portable match
        sql += " AND EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value = ?)"
        params.append(tag)

    # Exclude archived unless explicitly requested
    if not stage or stage != "archived":
        sql += " AND stage != 'archived'"

    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if json_out:
        _print_json([dict(r) for r in rows])
        return

    if not rows:
        ch.info("No work items found.")
        return

    # Table output
    headers = ["ID", "Slug", "Kind", "Stage", "Title"]
    col_widths = [4, 28, 12, 10, 36]
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    ch.info(header_line)
    ch.raw_print("-" * len(header_line))
    for r in rows:
        line = "  ".join(
            [
                str(r["id"]).ljust(col_widths[0]),
                str(r["slug"])[: col_widths[1]].ljust(col_widths[1]),
                str(r["kind"])[: col_widths[2]].ljust(col_widths[2]),
                str(r["stage"])[: col_widths[3]].ljust(col_widths[3]),
                str(r["title"])[: col_widths[4]],
            ]
        )
        ch.raw_print(line)


@work_app.command("show")
def cmd_show(
    slug_or_id: str = typer.Argument(..., help="Slug or numeric ID of the work item."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
):
    """Show details of a work item."""
    conn = _get_conn()

    if slug_or_id.isdigit():
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (int(slug_or_id),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM work_items WHERE slug = ?", (slug_or_id,)).fetchone()

    if not row:
        ch.error(f"Work item not found: {slug_or_id!r}")
        raise typer.Exit(code=1)

    events = conn.execute(
        "SELECT event_type, payload_json, created_at FROM work_events "
        "WHERE work_item_id = ? ORDER BY created_at ASC",
        (row["id"],),
    ).fetchall()

    if json_out:
        data = dict(row)
        data["events"] = [dict(e) for e in events]
        _print_json(data)
        return

    tags = json.loads(row["tags_json"] or "[]")
    ch.info(f"[{row['id']}] {row['title']}")
    ch.raw_print(f"  Slug   : {row['slug']}")
    ch.raw_print(f"  Kind   : {row['kind']}")
    ch.raw_print(f"  Stage  : {row['stage']}")
    ch.raw_print(f"  Owner  : {row['owner'] or '—'}")
    ch.raw_print(f"  Tags   : {', '.join(tags) if tags else '—'}")
    ch.raw_print(f"  Notes  : {row['notes_path'] or '—'}")
    ch.raw_print(f"  Created: {row['created_at']}")
    ch.raw_print(f"  Updated: {row['updated_at']}")
    if row["archived_at"]:
        ch.raw_print(f"  Archived: {row['archived_at']}")

    if events:
        ch.raw_print("")
        ch.info("History")
        for ev in events:
            payload = json.loads(ev["payload_json"] or "{}")
            detail = ", ".join(f"{k}={v}" for k, v in payload.items()) if payload else ""
            ch.raw_print(
                f"  {ev['created_at'][:19]}  {ev['event_type']}"
                + (f"  ({detail})" if detail else "")
            )


@work_app.command("move")
def cmd_move(
    slug_or_id: str = typer.Argument(..., help="Slug or numeric ID."),
    to: str = typer.Option(..., "--to", help=f"Target stage: {', '.join(VALID_STAGES)}."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
):
    """Move a work item to a new stage."""
    target = to.lower()
    if target not in VALID_STAGES:
        ch.error(f"Invalid stage '{target}'. Choose from: {', '.join(VALID_STAGES)}")
        raise typer.Exit(code=1)

    conn = _get_conn()

    if slug_or_id.isdigit():
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (int(slug_or_id),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM work_items WHERE slug = ?", (slug_or_id,)).fetchone()

    if not row:
        ch.error(f"Work item not found: {slug_or_id!r}")
        raise typer.Exit(code=1)

    old_stage = row["stage"]
    now = now_iso()

    archived_at = now if target == "archived" else row["archived_at"]

    conn.execute(
        "UPDATE work_items SET stage = ?, updated_at = ?, archived_at = ? WHERE id = ?",
        (target, now, archived_at, row["id"]),
    )
    _record_event(conn, row["id"], "moved", {"from": old_stage, "to": target})
    conn.commit()

    # Warn on significant backward moves
    if _STAGE_ORDER.get(target, 0) < _STAGE_ORDER.get(old_stage, 0) - 1:
        ch.warning(f"Moved backwards: {old_stage} → {target}")
    else:
        if json_out:
            _print_json({"slug": row["slug"], "from": old_stage, "to": target})
        else:
            ch.success(f"Moved {row['slug']!r}: {old_stage} → {target}")

    # Update wiki frontmatter if a note exists
    if row["notes_path"]:
        _update_wiki_stage(row["notes_path"], target)


def _update_wiki_stage(notes_path: str, new_stage: str) -> None:
    """Update the ``stage:`` field in the wiki note's frontmatter."""
    try:
        data_root = _NAVIG_ROOT
        full_path = data_root / "wiki" / notes_path
        if not full_path.exists():
            return
        content = full_path.read_text(encoding="utf-8")
        updated = re.sub(r"(?m)^stage:\s*\S+", f"stage: {new_stage}", content, count=1)
        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=full_path.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with _os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(updated)
            _os.replace(_tmp_path, full_path)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)
    except Exception as _exc:  # noqa: BLE001
        _log.debug("wiki stage update skipped: %s", _exc)


@work_app.command("update")
def cmd_update(
    slug_or_id: str = typer.Argument(..., help="Slug or numeric ID."),
    title: str | None = typer.Option(None, "--title", help="New title."),
    owner: str | None = typer.Option(None, "--owner", "-o", help="New owner."),
    tag: list[str] = typer.Option([], "--tag", "-t", help="Replace all tags (repeatable)."),
    ref_type: str | None = typer.Option(
        None, "--ref-type", help="External reference type (e.g. github, jira)."
    ),
    ref_id: str | None = typer.Option(None, "--ref-id", help="External reference ID."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
):
    """Update fields on a work item."""
    conn = _get_conn()

    if slug_or_id.isdigit():
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (int(slug_or_id),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM work_items WHERE slug = ?", (slug_or_id,)).fetchone()

    if not row:
        ch.error(f"Work item not found: {slug_or_id!r}")
        raise typer.Exit(code=1)

    updates: dict = {}
    if title is not None:
        updates["title"] = title
    if owner is not None:
        updates["owner"] = owner
    if tag:
        updates["tags_json"] = json.dumps(list(tag))
    if ref_type is not None:
        updates["ref_type"] = ref_type
    if ref_id is not None:
        updates["ref_id"] = ref_id

    if not updates:
        ch.warning("Nothing to update — pass at least one option.")
        raise typer.Exit(code=0)

    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [row["id"]]
    conn.execute(f"UPDATE work_items SET {set_clause} WHERE id = ?", values)
    _record_event(
        conn,
        row["id"],
        "updated",
        {k: v for k, v in updates.items() if k != "updated_at"},
    )
    conn.commit()

    if json_out:
        updated_row = conn.execute("SELECT * FROM work_items WHERE id = ?", (row["id"],)).fetchone()
        _print_json(dict(updated_row))
    else:
        ch.success(f"Updated {row['slug']!r}")


@work_app.command("archive")
def cmd_archive(
    slug_or_id: str = typer.Argument(..., help="Slug or numeric ID."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
):
    """Archive a work item (moves to the 'archived' stage)."""
    conn = _get_conn()

    if slug_or_id.isdigit():
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (int(slug_or_id),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM work_items WHERE slug = ?", (slug_or_id,)).fetchone()

    if not row:
        ch.error(f"Work item not found: {slug_or_id!r}")
        raise typer.Exit(code=1)

    if row["stage"] == "archived":
        ch.warning(f"{row['slug']!r} is already archived.")
        raise typer.Exit(code=0)

    now = now_iso()
    conn.execute(
        "UPDATE work_items SET stage = 'archived', updated_at = ?, archived_at = ? WHERE id = ?",
        (now, now, row["id"]),
    )
    _record_event(conn, row["id"], "archived", {"prev_stage": row["stage"]})
    conn.commit()

    if row["notes_path"]:
        _update_wiki_stage(row["notes_path"], "archived")

    if json_out:
        _print_json({"slug": row["slug"], "archived_at": now})
    else:
        ch.success(f"Archived {row['slug']!r}")


@work_app.command("stages")
def cmd_stages():
    """List all valid stage names."""
    for s in VALID_STAGES:
        ch.raw_print(s)


@work_app.command("kinds")
def cmd_kinds():
    """List all valid item kind names."""
    for k in VALID_KINDS:
        ch.raw_print(k)
