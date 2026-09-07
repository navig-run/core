"""``navig todo`` — the same task list, from a terminal.

The Telegram card is where tasks get captured and ticked off on a phone; this is
where they get captured while you are already typing, and where an agent or a script
reads them (`--json`). Both read and write the SAME rows — there is one list, and a
second one that only some surfaces can see would be worse than no list at all.

This is also the board's first CLI. `get_board_store` had exactly one consumer (the
Deck route), so a broken board was previously only visible through a web UI.
"""

from __future__ import annotations

from typing import Any

import typer

from navig import console_helper as ch

todo_app = typer.Typer(
    help="Your personal task list — capture, schedule, remind, tick off.",
    no_args_is_help=False,
)


def _store() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


def _now():
    from navig.pim.clock import local_now  # noqa: PLC0415

    return local_now()


def _banner() -> None:
    """Say when the extension is off, before printing anything else.

    Switching it off keeps the tasks and stops the REMINDERS, so a list that looks
    perfectly healthy is exactly the state where "why didn't it tell me" happens.
    """
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    text = extension_banner(as_html=False)
    if text:
        ch.warning("Reminders are not being delivered", text)


def _rows(todos: list[dict[str, Any]], now) -> list[tuple[str, ...]]:
    from navig.pim.clock import from_utc_iso, to_local  # noqa: PLC0415
    from navig.pim.dates import format_local, humanize_delta  # noqa: PLC0415
    from navig.pim.render import bucket_of  # noqa: PLC0415

    out: list[tuple[str, ...]] = []
    for todo in todos:
        due = to_local(from_utc_iso(todo.get("due_at")))
        bucket = bucket_of(todo, now)
        state = {
            "overdue": "[red]● overdue[/red]",
            "today": "[yellow]● today[/yellow]",
            "soon": "[green]● soon[/green]",
            "later": "[dim]○ later[/dim]",
            "inbox": "[dim]○ inbox[/dim]",
            "done": "[dim]✓ done[/dim]",
        }.get(bucket, bucket)
        out.append((
            str(todo.get("id", ""))[:8],
            state,
            str(todo.get("title") or ""),
            str(todo.get("category") or "[dim]—[/dim]"),
            format_local(due, now) if due else "[dim]—[/dim]",
            humanize_delta(due, now) if due else "",
            str(todo.get("recur") or ""),
        ))
    return out


@todo_app.command("list")
def list_cmd(
    category: str = typer.Option(None, "--category", "-c", help="Only this category."),
    space: str = typer.Option(None, "--space", "-s", help="Only tasks linked to this space."),
    all_todos: bool = typer.Option(False, "--all", "-a", help="Include completed tasks."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show your tasks, soonest first."""
    store = _store()
    todos = store.list_todos(category=category, space=space, include_done=all_todos)

    if as_json:
        ch.emit_json({"todos": todos, "count": len(todos)})
        return

    _banner()
    if not todos:
        ch.info("Nothing on the list.", "Add one: navig todo add \"Dentist tomorrow 10:30\"")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    now = _now()
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("State", no_wrap=True)
    # The one wrappable column, so a narrow terminal degrades by folding the title
    # rather than truncating the state and date columns that carry the meaning.
    table.add_column("Task")
    table.add_column("Category", no_wrap=True)
    table.add_column("Due", no_wrap=True)
    table.add_column("Left", no_wrap=True, style="dim")
    table.add_column("Repeat", no_wrap=True, style="dim")
    for row in _rows(todos, now):
        table.add_row(*row)
    ch.console.print(table)

    open_count = sum(1 for t in todos if not t.get("completed_at"))
    ch.dim(f"{open_count} open · tick one off with navig todo done <id>")


@todo_app.command("add")
def add_cmd(
    text: list[str] = typer.Argument(..., help='e.g. "Dentist tomorrow 10:30"'),
    category: str = typer.Option("", "--category", "-c", help="life · business · project · …"),
    space: str = typer.Option(None, "--space", "-s", help="Link it to a space."),
    remind: list[str] = typer.Option(
        None, "--remind", "-r", help="Lead time, repeatable: -r 3d -r 1h"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Capture a task. The date can be part of the sentence."""
    from navig.pim.clock import to_utc_iso  # noqa: PLC0415
    from navig.pim.dates import split_due, split_recurrence  # noqa: PLC0415

    line = " ".join(text).strip()
    if not line:
        ch.error("Nothing to add.", 'Try: navig todo add "Dentist tomorrow 10:30"')
        raise typer.Exit(1)

    now = _now()
    # Recurrence first: "every monday 9:30" must lose "every monday" before the date
    # parser sees it, or the suffix rule matches "monday 9:30" and the recurrence
    # stays in the title.
    rest, recur = split_recurrence(line)
    title, due = split_due(rest, now)
    if not title.strip():
        ch.error("That is a date with no task.", f'Try: navig todo add "Something {line}"')
        raise typer.Exit(1)

    todo = _store().create_todo(
        title.strip(),
        category=category,
        due_at=to_utc_iso(due) if due else None,
        recur=recur,
        remind_before=list(remind or []),
        space=space,
    )

    if as_json:
        ch.emit_json(todo)
        return

    from navig.pim.dates import format_local  # noqa: PLC0415

    if due:
        ch.success(f"Added: {todo['title']}", f"due {format_local(due, now)}")
    else:
        ch.success(f"Added to the inbox: {todo['title']}", "give it a date: navig todo when <id> <when>")
    if recur:
        ch.dim(f"repeats {recur}")
    _banner()


@todo_app.command("when")
def when_cmd(
    todo_id: str = typer.Argument(..., help="Task id (the first 8 characters are enough)."),
    when: list[str] = typer.Argument(..., help='e.g. "next monday 9am"'),
) -> None:
    """Give a task a date — or move the one it has."""
    from navig.pim.clock import to_utc_iso  # noqa: PLC0415
    from navig.pim.dates import format_local, split_due  # noqa: PLC0415

    store = _store()
    todo = _resolve(store, todo_id)
    now = _now()

    # The parser takes a date off the END of a line, so a bare "next monday" needs a
    # word in front of it to have an end to take it off. The task's own title is the
    # honest choice — it is what the operator would have typed.
    phrase = " ".join(when).strip()
    _, due = split_due(f"{todo['title']} {phrase}", now)
    if due is None:
        ch.error(f"I could not read a date in {phrase!r}.", "Try: tomorrow 9am · next monday · sep 12")
        raise typer.Exit(1)

    updated = store.update_todo(todo["id"], {"due_at": to_utc_iso(due)})
    ch.success(f"{updated['title']}", f"due {format_local(due, now)}")
    ch.dim("reminders are delivered through Telegram — set them there or with /todo")


@todo_app.command("done")
def done_cmd(todo_id: str = typer.Argument(..., help="Task id.")) -> None:
    """Tick a task off. A recurring one rolls forward instead."""
    store = _store()
    todo = _resolve(store, todo_id)
    updated = store.complete_todo(todo["id"])
    if updated is None:
        ch.error("That task is gone.")
        raise typer.Exit(1)

    if updated.get("completed_at"):
        ch.success(f"Done: {updated['title']}")
        return

    from navig.pim.clock import from_utc_iso, to_local  # noqa: PLC0415
    from navig.pim.dates import format_local  # noqa: PLC0415

    nxt = to_local(from_utc_iso(updated.get("due_at")))
    ch.success(
        f"Done: {updated['title']}",
        f"repeats {updated.get('recur')} — next on {format_local(nxt, _now())}" if nxt else "",
    )


@todo_app.command("rm")
def rm_cmd(
    todo_id: str = typer.Argument(..., help="Task id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Delete a task for good."""
    store = _store()
    todo = _resolve(store, todo_id)
    if not yes and not typer.confirm(f"Delete {todo['title']!r}?"):
        ch.info("Left alone.")
        return
    if not store.delete_todo(todo["id"]):
        ch.error("That task is gone.")
        raise typer.Exit(1)
    ch.success(f"Deleted: {todo['title']}")


@todo_app.command("categories")
def categories_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """Your categories and how much is open in each."""
    rows = _store().todo_categories()
    if as_json:
        ch.emit_json({"categories": rows})
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Category", no_wrap=True)
    table.add_column("Open", no_wrap=True)
    for row in rows:
        count = int(row.get("open") or 0)
        table.add_row(
            str(row.get("name") or "[dim]uncategorised[/dim]"),
            f"[green]{count}[/green]" if count else "[dim]—[/dim]",
        )
    ch.console.print(table)
    ch.dim("filter with navig todo list --category <name>")


@todo_app.command("scan")
def scan_cmd(
    space: str = typer.Option(None, "--space", "-s", help="Only this space."),
    add: bool = typer.Option(False, "--add", help="Add them as suggestions to confirm."),
    per_space: int = typer.Option(5, "--per-space", help="Cap per space."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Find `- [ ]` work already written down in your spaces.

    Read-only by default: it SHOWS what it found. `--add` puts them on the list marked
    as suggestions (they render with a sparkle in `/todo`), for you to keep or dismiss
    one tap at a time. Anything already added — or previously dismissed — is
    skipped, so running this twice never proposes the same line again.
    """
    from navig.pim.spaces_scan import discover, scan_spaces  # noqa: PLC0415

    spaces = discover()
    if space:
        spaces = {k: v for k, v in spaces.items() if k == space}
        if not spaces:
            ch.error(f"No space called {space!r}.", "See them with navig space list")
            raise typer.Exit(1)

    store = _store()
    found = scan_spaces(spaces, per_space=per_space, store=store)

    if as_json:
        ch.emit_json({
            "suggestions": [
                {"title": s.title, "space": s.space, "source": s.origin_ref} for s in found
            ],
            "count": len(found),
            "spaces_scanned": len(spaces),
        })
        return

    if not found:
        ch.info(
            f"Nothing new across {len(spaces)} space(s).",
            "Everything already checked off, on the list, or dismissed.",
        )
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Space", no_wrap=True)
    table.add_column("Task")
    table.add_column("From", no_wrap=True, style="dim")
    for suggestion in found:
        table.add_row(
            suggestion.space,
            suggestion.title,
            f"{suggestion.source_file}:{suggestion.line_no}",
        )
    ch.console.print(table)

    if not add:
        ch.dim(f"{len(found)} found · add them with navig todo scan --add")
        return

    added = 0
    for suggestion in found:
        try:
            store.create_todo(
                suggestion.title,
                space=suggestion.space,
                origin="agent",
                origin_ref=suggestion.origin_ref,
            )
            added += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not lose the rest
            ch.warning(f"Could not add {suggestion.title!r}", str(exc))
    ch.success(f"Added {added} suggestion(s).", "keep or dismiss them in /todo or navig todo list")


def _resolve(store: Any, todo_id: str) -> dict[str, Any]:
    """Find a task by id or unambiguous id prefix.

    The list prints eight characters, so eight is what people type back. An ambiguous
    prefix is an ERROR rather than a first match: silently acting on the wrong task is
    the one outcome a task list must never produce.
    """
    exact = store.get_todo(todo_id)
    if exact is not None:
        return exact

    matches = [t for t in store.list_todos(include_done=True) if str(t["id"]).startswith(todo_id)]
    if not matches:
        ch.error(f"No task starting with {todo_id!r}.", "See them with navig todo list")
        raise typer.Exit(1)
    if len(matches) > 1:
        ch.error(
            f"{todo_id!r} matches {len(matches)} tasks.",
            " · ".join(f"{t['id'][:8]} {t['title']}" for t in matches[:5]),
        )
        raise typer.Exit(1)
    return matches[0]


@todo_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """`navig todo` with no verb shows the list — the thing you want 90% of the time."""
    if ctx.invoked_subcommand is None:
        list_cmd(category=None, space=None, all_todos=False, as_json=False)
