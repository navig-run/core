"""navig checkin — scheduled check-ins: the weigh-in, pills, water.

The terminal side of :mod:`navig.checkins`. The bot asks on a schedule, the
operator answers with one tap; this command adds, pauses, logs and reports.

    navig checkin list
    navig checkin add pills --name "Metformin" --dose "500 mg" --at 08:00,20:00
    navig checkin add water --target 8 --every 2h --from 9 --to 21
    navig checkin pause metformin · resume metformin · remove metformin
    navig checkin log metformin --slot 08:00            # record a dose by hand
    navig checkin stats
    navig checkin migrate --dry-run                      # old weigh-in jobs → checkin:weigh

Records only. Nothing here gives — or computes — dose advice; the dose is the
text you typed, shown back verbatim.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import typer

from navig import console_helper as ch

checkin_app = typer.Typer(
    name="checkin",
    help="Scheduled check-ins — weigh-in, pills, water",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _fail(message: str, as_json: bool, details: str | None = None) -> NoReturn:
    if as_json:
        ch.emit_json({"ok": False, "error": message})
    else:
        ch.error(message, details)
    raise typer.Exit(1)


def _chat(chat_id: int | None) -> int | None:
    if chat_id:
        return int(chat_id)
    from navig.checkins.jobs import _default_chat

    return _default_chat()


@checkin_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Show today's check-ins when called with no subcommand."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(checkin_list)


@checkin_app.command("list")
def checkin_list(
    chat_id: int | None = typer.Option(
        None, "--chat-id", help="Telegram chat whose record to read"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Every check-in, its schedule and today's state."""
    from navig.checkins import engine

    entries = engine.today_entries(_chat(chat_id))
    if as_json:
        ch.emit_json({"ok": True, "checkins": entries})
        return
    table = ch.create_table(
        title="Check-ins",
        columns=[
            {"name": "ID", "style": "cyan"},
            {"name": "What"},
            {"name": "Schedule"},
            {"name": "Today"},
            {"name": "Status"},
        ],
    )
    for e in entries:
        if e["template"] == "weigh":
            schedule = "morning (navig body cadence)"
        elif e["template"] == "water":
            schedule = (
                f"every {e['every_hours']}h {e['from_hour']:02d}–{e['to_hour']:02d}"
                if e.get("every_hours")
                else "no reminders"
            ) + f" · target {e['target']}"
        else:
            schedule = ", ".join(e.get("slots") or [])
        status = "[green]● on[/green]" if e.get("enabled", True) else "[dim]○ paused[/dim]"
        table.add_row(
            e["id"],
            f"{e['emoji']} {e['label']}",
            schedule,
            ("[green]✓[/green] " if e.get("done") else "") + str(e.get("state") or "—"),
            status,
        )
    ch.print_table(table)
    if entries and entries[0].get("unavailable"):
        ch.warning("The check-in record could not be read.", str(entries[0]["unavailable"]))
    ch.dim(f"{len(entries)} check-in(s) · add one: navig checkin add pills --name … --at 08:00")


add_app = typer.Typer(help="Add a check-in (pills or water)", no_args_is_help=True)
checkin_app.add_typer(add_app, name="add")


def _save_and_schedule(item, chat: int | None, as_json: bool) -> None:
    from navig.checkins import defs as d
    from navig.checkins import jobs

    if not chat:
        _fail("No Telegram chat id.", as_json, "Set telegram.allowed_users or pass --chat-id.")
    taken = {i.id for i in d.load_items()}
    from dataclasses import replace

    item = replace(item, id=d.unique_id(item.id, taken))
    try:
        saved = d.save_item(item)
    except ValueError as exc:
        _fail(str(exc), as_json)
    try:
        specs = jobs.sync_jobs(saved, int(chat))
    except Exception as exc:  # noqa: BLE001
        _fail(
            f"saved {saved.id}, but its schedule could not be written: {exc}",
            as_json,
            f"Retry with: navig checkin resume {saved.id}",
        )
    if as_json:
        ch.emit_json({"ok": True, "checkin": saved.as_dict(), "jobs": specs})
        return
    ch.success(f"Check-in added: {saved.id} — {saved.definition.emoji} {saved.label()}")
    for spec in specs:
        ch.dim(f"  {spec['name']}  ({spec['schedule']})")
    if saved.template == "pills":
        ch.dim("  The bot never marks a dose taken by itself and never gives dose advice.")


@add_app.command("pills")
def add_pills(
    name: str = typer.Option(..., "--name", help="The medication, as you call it"),
    dose: str = typer.Option("", "--dose", help="Dose text, shown back exactly as typed"),
    at: str = typer.Option(..., "--at", help="Times of day, e.g. 08:00,20:00"),
    missed_after: int = typer.Option(
        120, "--missed-after", help="Minutes before one 'still open' nudge (15-720)"
    ),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to ask"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Add a medication check-in — one prompt per time of day."""
    from navig.checkins import defs as d

    try:
        item = d.validate(
            "pills", {"name": name, "dose": dose, "times": at, "missed_after": missed_after}
        )
    except ValueError as exc:
        _fail(str(exc), as_json)
    _save_and_schedule(item, _chat(chat_id), as_json)


@add_app.command("water")
def add_water(
    target: int = typer.Option(8, "--target", help="Glasses a day (1-30)"),
    every: str = typer.Option(
        "off", "--every", help="Remind every N hours, e.g. 2h ('off' = none)"
    ),
    from_hour: int = typer.Option(9, "--from", help="First reminder hour (0-23)"),
    to_hour: int = typer.Option(21, "--to", help="No reminders from this hour on (1-24)"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to ask"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Add the water check-in — a daily target and optional reminders."""
    from navig.checkins import defs as d

    if d.get_item(d.WATER_ID) is not None:
        _fail(
            "A water check-in already exists.",
            as_json,
            "Remove it first: navig checkin remove water",
        )
    try:
        item = d.validate(
            "water",
            {"target": target, "every": every, "from_hour": from_hour, "to_hour": to_hour},
        )
    except ValueError as exc:
        _fail(str(exc), as_json)
    _save_and_schedule(item, _chat(chat_id), as_json)


def _require(item_id: str, as_json: bool):
    from navig.checkins import defs as d

    item = d.get_item(item_id)
    if item is None:
        _fail(f"No check-in {item_id!r}.", as_json, "See them all: navig checkin list")
    return item


@checkin_app.command("remove")
def checkin_remove(
    item_id: str = typer.Argument(..., help="Check-in id (navig checkin list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Remove a check-in and its schedule. Answers already recorded are kept."""
    from navig.checkins import defs as d
    from navig.checkins import jobs, state

    if item_id == d.WEIGH_ID:
        _fail("The weigh-in is built in.", as_json, "Turn it off with: navig checkin pause weigh")
    item = _require(item_id, as_json)
    if not yes and not as_json and not ch.confirm_action(f"Remove {item.label()}?", default=False):
        ch.dim("Nothing removed.")
        return
    removed_jobs = jobs.remove_jobs(item.id)
    d.remove_item(item.id)
    state.forget(item.id)
    if as_json:
        ch.emit_json({"ok": True, "removed": item.id, "jobs_removed": removed_jobs})
        return
    ch.success(f"Removed {item.id} ({removed_jobs} scheduled job(s)). Its records are kept.")


def _toggle(item_id: str, enabled: bool, chat_id: int | None, as_json: bool) -> None:
    from navig.checkins import defs as d
    from navig.checkins import jobs

    item = _require(item_id, as_json)
    try:
        saved = d.set_enabled(item.id, enabled)
        if item.template != "weigh":
            chat = _chat(chat_id)
            if enabled and chat:
                jobs.sync_jobs(saved, int(chat))  # re-creates jobs a past removal lost
            else:
                jobs.set_jobs_enabled(item.id, enabled)
    except Exception as exc:  # noqa: BLE001
        _fail(f"could not {'resume' if enabled else 'pause'} {item.id}: {exc}", as_json)
    if as_json:
        ch.emit_json({"ok": True, "checkin": saved.as_dict()})
        return
    ch.success(f"{saved.id} {'resumed' if enabled else 'paused'}.")


@checkin_app.command("pause")
def checkin_pause(
    item_id: str = typer.Argument(..., help="Check-in id"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Stop asking. Nothing is deleted; resume restores it exactly."""
    _toggle(item_id, False, None, as_json)


@checkin_app.command("resume")
def checkin_resume(
    item_id: str = typer.Argument(..., help="Check-in id"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to ask"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Start asking again."""
    _toggle(item_id, True, chat_id, as_json)


@checkin_app.command("log")
def checkin_log(
    item_id: str = typer.Argument(..., help="Check-in id"),
    slot: str | None = typer.Option(None, "--slot", help="Pills: which dose time, e.g. 08:00"),
    status: str = typer.Option("taken", "--status", help="Pills: taken | skipped"),
    at: str | None = typer.Option(None, "--at", help="Pills: when it was taken, HH:MM"),
    glasses: int = typer.Option(1, "--glasses", help="Water: how many to add (negative undoes)"),
    day: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (default: today)"),
    force: bool = typer.Option(
        False, "--force", help="Pills: record a second 'taken' for one dose"
    ),
    chat_id: int | None = typer.Option(
        None, "--chat-id", help="Telegram chat whose record to write"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Record an answer by hand (the weigh-in has `navig body log --weight`)."""
    from navig.checkins import cadence, records
    from navig.checkins import defs as d

    item = _require(item_id, as_json)
    if item.template == "weigh":
        _fail("Log a weight with: navig body log --weight 87.4", as_json)
    tz = cadence.local_tz()
    now = cadence.local_now(tz)
    try:
        when = date.fromisoformat(day) if day else now.date()
    except ValueError:
        _fail(f"--date must be YYYY-MM-DD, got {day!r}", as_json)
    path = records.records_path(_chat(chat_id))
    try:
        rows = records.read(path)
    except records.RecordsReadError as exc:
        _fail(str(exc), as_json)

    if item.template == "water":
        rec = records.append(
            path,
            {
                "date": when.isoformat(),
                "item": item.id,
                "kind": d.KIND_COUNT,
                "status": records.ADD,
                "value": int(glasses),
                "source": "cli",
            },
        )
        total = records.count_total(rows + [rec], item.id, when.isoformat())
        if as_json:
            ch.emit_json({"ok": True, "record": rec, "total": total, "target": item.target})
            return
        ch.success(f"Water {when.isoformat()}: {total}/{item.target}")
        return

    if status not in (records.TAKEN, records.SKIPPED):
        _fail("--status must be taken or skipped", as_json)
    slots = item.slots()
    if slot:
        try:
            wanted = d.parse_time(slot).replace(":", "")
        except ValueError as exc:
            _fail(str(exc), as_json)
        if wanted not in slots:
            _fail(
                f"{item.id} has no {slot} dose — its times: {', '.join(cards_fmt(slots))}", as_json
            )
    elif len(slots) == 1:
        wanted = slots[0]
    else:
        _fail(f"{item.id} has several doses — pass --slot ({', '.join(cards_fmt(slots))})", as_json)
    if (
        status == records.TAKEN
        and records.taken(rows, item.id, when.isoformat(), wanted)
        and not force
    ):
        _fail(
            f"{item.label()} {cards_fmt([wanted])[0]} is already recorded as taken on {when}.",
            as_json,
            "Record a second dose anyway with --force.",
        )
    taken_at = now
    if at:
        try:
            hhmm = d.parse_time(at)
        except ValueError as exc:
            _fail(str(exc), as_json)
        taken_at = datetime.combine(when, cadence.parse_slot(hhmm)).replace(tzinfo=tz)
    rec = records.append(
        path,
        {
            "date": when.isoformat(),
            "item": item.id,
            "kind": d.KIND_DOSE,
            "slot": wanted,
            "status": status,
            "at": records.utc_iso(taken_at),
            "source": "cli",
            "name": item.name,
            "dose": item.dose,
        },
    )
    if as_json:
        ch.emit_json({"ok": True, "record": rec})
        return
    ch.success(f"{item.label()} {cards_fmt([wanted])[0]} — {status} ({when.isoformat()})")


def cards_fmt(slots: list[str]) -> list[str]:
    from navig.checkins.cards import fmt_slot

    return [fmt_slot(s) for s in slots]


@checkin_app.command("stats")
def checkin_stats(
    days: int = typer.Option(7, "--days", help="Window in days (1-90)"),
    chat_id: int | None = typer.Option(
        None, "--chat-id", help="Telegram chat whose record to read"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Pill adherence and water totals over the last N days."""
    from navig.checkins import cadence, records
    from navig.checkins import defs as d

    if not 1 <= days <= 90:
        _fail("--days must be 1-90", as_json)
    now = cadence.local_now()
    try:
        rows = records.read(records.records_path(_chat(chat_id)))
    except records.RecordsReadError as exc:
        _fail(str(exc), as_json)
    out = []
    for item in d.load_items(include_weigh=False):
        if item.template == "pills":
            a = records.adherence(
                rows,
                item.id,
                item.slots(),
                today=now.date(),
                days=days,
                since=item.created_date(),
                now_local=now,
            )
            out.append({"id": item.id, "label": item.label(), "template": "pills", **a})
        else:
            totals = [
                records.count_total(rows, item.id, (now.date() - timedelta(days=i)).isoformat())
                for i in range(days)
            ]
            hit = sum(1 for x in totals if x >= item.target)
            out.append(
                {
                    "id": item.id,
                    "label": "water",
                    "template": "water",
                    "days": days,
                    "target": item.target,
                    "average": round(sum(totals) / days, 1),
                    "days_on_target": hit,
                }
            )
    if as_json:
        ch.emit_json({"ok": True, "days": days, "stats": out})
        return
    table = ch.create_table(
        title=f"Check-ins · last {days} days",
        columns=[{"name": "ID", "style": "cyan"}, {"name": "What"}, {"name": "Result"}],
    )
    for s in out:
        if s["template"] == "pills":
            pct = "—" if s["percent"] is None else f"{s['percent']}%"
            result = (
                f"{pct} taken · {s['taken']}/{s['scheduled']} "
                f"[dim](skipped {s['skipped']}, no answer {s['missed']})[/dim]"
            )
        else:
            result = f"avg {s['average']}/{s['target']} · on target {s['days_on_target']}/{s['days']} days"
        table.add_row(s["id"], s["label"], result)
    ch.print_table(table)
    if not out:
        ch.dim("No pills or water check-ins yet · navig checkin add pills --name … --at 08:00")


@checkin_app.command("migrate")
def checkin_migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan; change nothing"),
    store: Path | None = typer.Option(
        None, "--store", help="cron_jobs.json (default: the live store)"
    ),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Chat for a job that names none"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Move the old weigh-in jobs (habit:weigh / habit:bodyreview) onto check-ins.

    Converts them in place to `checkin:weigh` / `checkin:weigh:weekly`, run in the
    scheduler's own process with a 180 s timeout. Same schedule, same chat, same
    on/off. A timestamped backup of the store is written first; running it twice
    changes nothing the second time.
    """
    from navig.checkins import jobs
    from navig.platform.paths import resolve_user_path
    from navig.scheduler import habit_store

    path = resolve_user_path(str(store)) if store else habit_store.live_store_path()
    try:
        result = jobs.migrate_legacy_jobs(path, dry_run=dry_run, default_chat=_chat(chat_id))
    except Exception as exc:  # noqa: BLE001
        _fail(f"migration failed: {exc}", as_json)
    if as_json:
        ch.emit_json(result)
        return
    changes = result.get("changes") or []
    if not changes:
        ch.success(f"Nothing to migrate in {result['store']}.")
    else:
        table = ch.create_table(
            title=("Planned" if dry_run else "Migrated") + " weigh-in jobs",
            columns=[
                {"name": "Job", "style": "cyan"},
                {"name": "Change"},
                {"name": "Before"},
                {"name": "After"},
            ],
        )
        for c in changes:
            before = c["from"]
            after = c["to"]
            table.add_row(
                str(c.get("id")),
                c["action"],
                f"{before['name']} · {before['schedule']} · {before['timeout_seconds']}s\n[dim]{before['command']}[/dim]",
                (
                    f"{after['name']} · {after['schedule']} · {after['timeout_seconds']}s\n[dim]{after['command']}[/dim]"
                    if after
                    else "[dim]removed[/dim]"
                ),
            )
        ch.print_table(table)
    for s in result.get("skipped") or []:
        ch.warning(f"Skipped {s.get('name')}: {s.get('reason')}", "Pass --chat-id to migrate it.")
    if result.get("backup"):
        ch.dim(f"Backup: {result['backup']}")
    if dry_run and changes:
        ch.dim("Dry run — nothing changed. Apply with: navig checkin migrate")
