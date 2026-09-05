"""navig body — the body record a space owns (metrics.csv).

The counterpart to ``navig habit``: habits are things you *do*, body metrics are
things you *measure*. They live in different files, and in this operator's setup
in different spaces — the growth space keeps the discipline, the health space
keeps the numbers and the medicine.

**Why this is not ``navig health``:** that name is already an alias for
``navig stack``, which runs cross-service *system* health checks. Two different
meanings of "health" under one verb would be a trap for whoever typed it next.

Storage goes through :mod:`navig.spaces.body_metrics`, which the Telegram card
also writes through — the gateway must not import this module to record a tap.

Never diagnoses, never prescribes, never suggests a dose. Where a number is
unusual it says so neutrally and points at a real appointment.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.spaces import body_metrics as bm

body_app = typer.Typer(
    name="body",
    help="Track body metrics — weight, sleep, mood — and see the trend",
    invoke_without_command=True,
    no_args_is_help=False,
)

#: Re-exported for readability at the two call sites below. The value lives in
#: body_metrics so the CLI and the Telegram card cannot disagree about when the
#: line appears. It is a threshold for SAYING SOMETHING NEUTRAL, never for
#: interpreting anything.
_FAST_LOSS_KG_PER_WEEK = bm.FAST_LOSS_KG_PER_WEEK


def _resolve(space: str | None) -> Path:
    return bm.metrics_path(space)


def _today(day: str | None) -> str:
    if not day:
        return date.today().isoformat()
    try:
        return datetime.strptime(day, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise typer.BadParameter(f"--date must be YYYY-MM-DD, got {day!r}") from exc


def _clinician_line(trend: float | None) -> str | None:
    """One neutral sentence when the trend is fast. Never an interpretation."""
    if trend is None or trend > -_FAST_LOSS_KG_PER_WEEK:
        return None
    return (
        f"Down {abs(trend):.1f} kg in a week — a fast rate. Worth mentioning at your "
        "next appointment; not something to act on here."
    )


@body_app.callback(invoke_without_command=True)
def _body_default(ctx: typer.Context) -> None:
    """Show today's record when called with no subcommand."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(body_today)


@body_app.command("log")
def body_log(
    weight: float | None = typer.Option(None, "--weight", "-w", help="Weight in kg"),
    sleep: float | None = typer.Option(None, "--sleep", help="Hours slept"),
    mood: int | None = typer.Option(None, "--mood", help="Mood, 1-10"),
    steps: int | None = typer.Option(None, "--steps", help="Steps for the day"),
    note: str = typer.Option("", "--note", "-n", help="Free-text note for the day"),
    day: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (default: today)"),
    space: str | None = typer.Option(None, "--space", help="Space or metrics.csv path"),
    force: bool = typer.Option(
        False, "--force", help="Record a weight even if it looks implausible"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Record one or more measurements for a day."""
    path = _resolve(space)
    when = _today(day)

    values: dict[str, str] = {}
    if weight is not None:
        try:
            parsed = bm.parse_weight(str(weight))
        except bm.WeightParseError as exc:
            if as_json:
                ch.emit_json({"ok": False, "error": str(exc)})
            else:
                ch.error(str(exc))
            raise typer.Exit(1) from exc
        last = bm.latest(path)
        if not force and bm.is_implausible_jump(parsed, last[1] if last else None):
            msg = (
                f"{parsed:g} kg is more than {bm.IMPLAUSIBLE_JUMP_KG:g} kg from your "
                f"last reading ({last[1]:g} kg on {last[0]}). Nothing was recorded — "
                "re-run with --force if it is right."
            )
            if as_json:
                ch.emit_json({"ok": False, "error": msg, "needs_confirmation": True})
            else:
                ch.warning(msg)
            raise typer.Exit(1)
        values["weight_kg"] = f"{parsed:g}"
    if sleep is not None:
        values["sleep_hours"] = f"{sleep:g}"
    if mood is not None:
        if not 1 <= mood <= 10:
            raise typer.BadParameter("--mood must be between 1 and 10")
        values["mood_1_10"] = str(mood)
    if steps is not None:
        values["steps"] = str(steps)
    if note:
        values["notes"] = note

    if not values:
        if as_json:
            ch.emit_json({"ok": False, "error": "nothing to log"})
        else:
            ch.warning("Nothing to log — pass --weight, --sleep, --mood, --steps or --note.")
        raise typer.Exit(1)

    try:
        previous = bm.upsert(path, when, values)
    except bm.MetricsReadError as exc:
        if as_json:
            ch.emit_json({"ok": False, "error": str(exc)})
        else:
            ch.error(str(exc))
        raise typer.Exit(1) from exc

    if as_json:
        ch.emit_json(
            {
                "ok": True,
                "date": when,
                "path": str(path),
                "recorded": values,
                "previous": previous,
            }
        )
        return

    ch.success(f"Recorded for {when}: " + ", ".join(f"{k}={v}" for k, v in values.items()))
    replaced = {k: v for k, v in previous.items() if v}
    if replaced:
        ch.dim("  replaced: " + ", ".join(f"{k}={v}" for k, v in replaced.items()))
    ch.dim(f"  {path}")


@body_app.command("today")
def body_today(
    day: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (default: today)"),
    space: str | None = typer.Option(None, "--space", help="Space or metrics.csv path"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show what is recorded for a day, and where the week stands."""
    path = _resolve(space)
    when = _today(day)
    try:
        fieldnames, rows = bm.read_metrics(path)
    except bm.MetricsReadError as exc:
        if as_json:
            ch.emit_json({"ok": False, "error": str(exc)})
        else:
            ch.error(str(exc))
        raise typer.Exit(1) from exc

    row = next((r for r in rows if r.get("date") == when), {})
    summary = bm.summarize(path, ending=date.fromisoformat(when))

    if as_json:
        ch.emit_json({"ok": True, "date": when, "row": row, "week": summary})
        return

    if not path.exists():
        ch.warning(f"No metrics file yet at {path}")
        ch.dim("  Start with: navig body log --weight 87.4")
        return

    table = ch.create_table(
        title=f"Body — {when}",
        columns=[
            {"name": "Metric", "style": "cyan"},
            {"name": "Value", "justify": "right"},
        ],
    )
    labels = {
        "weight_kg": "Weight (kg)",
        "sleep_hours": "Sleep (h)",
        "mood_1_10": "Mood (1-10)",
        "steps": "Steps",
        "body_fat_pct": "Body fat (%)",
        "resting_hr": "Resting HR",
        "hrv": "HRV",
    }
    any_value = False
    for key, label in labels.items():
        if key not in fieldnames:
            continue
        value = (row.get(key) or "").strip()
        table.add_row(label, value if value else "[dim]—[/dim]")
        any_value = any_value or bool(value)
    ch.print_table(table)

    if (row.get("notes") or "").strip():
        ch.console.print(f"[dim]note:[/dim] {row['notes']}")

    if not any_value:
        ch.dim(f"Nothing recorded for {when} yet — navig body log --weight <kg>")

    _print_week(summary)


def _print_week(summary: dict) -> None:
    """The 7-day view every surface ends with."""
    avg, trend = summary.get("average"), summary.get("trend")
    recorded, days = summary.get("recorded"), summary.get("days")
    if avg is None:
        ch.dim(f"7-day average: not enough recorded ({recorded}/{days} days)")
        return
    if trend is None:
        arrow = "[dim]— no prior week[/dim]"
    elif trend < 0:
        arrow = f"[green]{trend:+.1f} kg[/green]"
    elif trend > 0:
        arrow = f"[yellow]{trend:+.1f} kg[/yellow]"
    else:
        arrow = "[dim]±0.0 kg[/dim]"
    spark = summary.get("sparkline") or ""
    ch.console.print(
        f"7-day average: [bold]{avg:g} kg[/bold]  {arrow}  {spark}  "
        f"[dim]logged {recorded}/{days}[/dim]"
    )
    line = _clinician_line(trend)
    if line:
        ch.console.print(f"[yellow]⚠[/yellow]  {line}")


@body_app.command("trend")
def body_trend(
    days: int = typer.Option(7, "--days", "-d", help="Window size in days"),
    field: str = typer.Option("weight_kg", "--field", help="Which column to trend"),
    space: str | None = typer.Option(None, "--space", help="Space or metrics.csv path"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show the moving average and the change against the previous window."""
    path = _resolve(space)
    try:
        summary = bm.summarize(path, field=field, days=days)
    except bm.MetricsReadError as exc:
        if as_json:
            ch.emit_json({"ok": False, "error": str(exc)})
        else:
            ch.error(str(exc))
        raise typer.Exit(1) from exc

    if as_json:
        ch.emit_json({"ok": True, **summary})
        return

    points = summary.get("points") or []
    if not points:
        ch.warning(f"No {field} recorded in the last {days} days.")
        ch.dim(f"  {path}")
        return

    table = ch.create_table(
        title=f"{field} — last {days} days",
        columns=[
            {"name": "Date", "style": "cyan", "justify": "left"},
            {"name": "Value", "justify": "right"},
        ],
    )
    for point in points:
        table.add_row(point["date"], f"{point['value']:g}")
    ch.print_table(table)
    _print_week(summary)


@body_app.command("export")
def body_export(
    days: int = typer.Option(90, "--days", "-d", help="How far back to export"),
    space: str | None = typer.Option(None, "--space", help="Space or metrics.csv path"),
    out: str | None = typer.Option(None, "--out", help="Destination file"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Write a dated summary to the space's out/ — what an appointment needs.

    ``ops/README.md`` already tells you to do this by hand before a lab or a
    doctor's visit. This is that step, with the numbers already in it.
    """
    path = _resolve(space)
    try:
        summary = bm.summarize(path, days=days)
    except bm.MetricsReadError as exc:
        if as_json:
            ch.emit_json({"ok": False, "error": str(exc)})
        else:
            ch.error(str(exc))
        raise typer.Exit(1) from exc

    today = date.today().isoformat()
    destination = Path(out) if out else path.parent / "out" / f"body-{today}.md"

    points = summary.get("points") or []
    lines = [
        f"# Body metrics — {today}",
        "",
        f"Window: last {days} days · {summary['recorded']} of {days} days recorded",
        "",
        # `— kg` is not a unit-less unknown, it is a nonsense measurement, and this
        # document is read at an appointment. An absent number prints as "—" with
        # no unit; a present one uses :g so 123 does not become 123.0 in the
        # header while the table below says 123.
        f"- Latest weight: "
        + (f"**{summary['latest']:g} kg**" if summary["latest"] is not None else "—")
        + (f" ({summary['latest_date']})" if summary["latest_date"] else ""),
        f"- {days}-day average: "
        + (f"**{summary['average']:g} kg**" if summary["average"] is not None else "—"),
        f"- Change vs previous window: "
        + (
            f"**{summary['trend']:+g} kg**"
            if summary["trend"] is not None
            else "— *(no earlier window to compare against)*"
        ),
        "",
        "| Date | Weight (kg) |",
        "|---|---|",
    ]
    lines += [f"| {p['date']} | {p['value']:g} |" for p in points]
    line = _clinician_line(summary.get("trend"))
    if line:
        lines += ["", f"> {line}"]
    lines += ["", "_Recorded by `navig body`. Not a medical document._", ""]

    from navig.core.yaml_io import atomic_write_text

    atomic_write_text(destination, "\n".join(lines), encoding="utf-8")

    if as_json:
        ch.emit_json({"ok": True, "path": str(destination), "days": days, **summary})
        return
    ch.success(f"Exported {summary['recorded']} day(s) to {destination}")


@body_app.command("checkin")
def body_checkin(
    send: bool = typer.Option(False, "--send", help="Send it to Telegram"),
    weekly: bool = typer.Option(
        False, "--weekly", help="The Sunday card (trend, mood, sleep, treatment)"
    ),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to send to"),
    day: str | None = typer.Option(None, "--date", help="Card for another day"),
    space: str | None = typer.Option(None, "--space", help="Space or metrics.csv path"),
) -> None:
    """Preview — or send to Telegram — the weigh-in prompt or the weekly card.

    Two shapes, because two questions:

      navig body checkin                    # preview the morning weigh-in
      navig body checkin --send             # ask for today's weight
      navig body checkin --weekly --send    # the Sunday trend + check-in card

    Deliver on a schedule by pointing cron jobs at this command:
      navig body checkin --send --space <space>            # 08:10 daily
      navig body checkin --weekly --send --space <space>   # 18:45 Sunday
    """
    from navig.telegram import body_actions

    path = _resolve(space)
    when = _today(day)

    # The SENDING path only. This process is not the gateway — cron shells out to
    # `navig body checkin --send`, which builds the card and calls sendMessage
    # directly — so the extension gate has to be applied here too, or "off" would
    # still write to the operator every morning. Preview stays allowed: reading
    # your own record in your own terminal is not a bot surface.
    if send:
        from navig.gateway.channels.telegram_extensions import is_enabled

        if not is_enabled("health"):
            ch.warning(
                "Nothing sent — the Health extension is off.",
                "Turn it on with /extensions in Telegram, or: "
                "navig telegram extensions enable health",
            )
            return

    if weekly:
        text, markup = body_actions.build_weekly_card(path, when)
    else:
        text, markup = body_actions.build_weigh_prompt(path, when)

    if not send:
        plain = text
        for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
            plain = plain.replace(tag, "")
        ch.console.print(plain)
        for row in markup.get("inline_keyboard", []):
            ch.console.print("  " + "   ".join(b["text"] for b in row))
        ch.dim(f"\n{path}")
        ch.dim("Send it with: navig body checkin --send")
        return

    target = chat_id or _default_chat_id()
    if not target:
        ch.error("No Telegram chat id.", "Set telegram.allowed_users or pass --chat-id.")
        raise typer.Exit(1)

    from navig.commands.telegram import _api_call, _load_telegram_token

    try:
        result = _api_call(
            _load_telegram_token(),
            "sendMessage",
            {
                "chat_id": target,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": markup,
            },
        )
    except Exception as exc:  # noqa: BLE001
        ch.error("Could not send the check-in.", str(exc))
        raise typer.Exit(1) from exc

    message_id = (result.get("result") or {}).get("message_id")

    # Sent from HERE, answered by the GATEWAY — a different process whose active
    # space is very likely something else. Record which metrics.csv this chat's
    # answers write into, and which message a typed reply must be a reply to.
    bm.remember_target(int(target), path, message_id=message_id, day=when)
    if not weekly:
        bm.set_prompt(int(target), "weigh", when, message_id)

    ch.success(f"{'Weekly card' if weekly else 'Weigh-in'} sent to {target}.")
    ch.dim(f"{path}")


def _default_chat_id() -> int | None:
    """First allowed Telegram user, the same rule `navig habit` uses."""
    try:
        from navig.config import get_config_manager

        allowed = get_config_manager().get("telegram.allowed_users", default=[]) or []
        return int(allowed[0]) if allowed else None
    except Exception:  # noqa: BLE001
        return None
