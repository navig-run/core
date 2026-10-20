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
from typing import NoReturn

import typer

from navig import console_helper as ch
from navig.platform.paths import resolve_user_path
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


def _now() -> datetime:
    """The local wall clock — one seam, so tests can pin the cadence rules' "now"."""
    return datetime.now()


def _today(day: str | None) -> str:
    if not day:
        return _now().date().isoformat()
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

    # A weight logged here answers the morning's Telegram question too. Leaving
    # that prompt pending keeps its force_reply box re-arming in the client.
    cleared = _dismiss_pending_weigh(path, when) if "weight_kg" in values else 0

    if as_json:
        ch.emit_json(
            {
                "ok": True,
                "date": when,
                "path": str(path),
                "recorded": values,
                "previous": previous,
                "prompts_cleared": cleared,
            }
        )
        return

    ch.success(f"Recorded for {when}: " + ", ".join(f"{k}={v}" for k, v in values.items()))
    replaced = {k: v for k, v in previous.items() if v}
    if replaced:
        ch.dim("  replaced: " + ", ".join(f"{k}={v}" for k, v in replaced.items()))
    if cleared:
        ch.dim("  cleared the pending weigh-in question in Telegram")
    ch.dim(f"  {path}")


def _dismiss_pending_weigh(path: Path, day: str) -> int:
    """Clear (and delete in Telegram) any weigh prompt for *day* that writes into *path*.

    Best-effort and never raises: the weight is already recorded, and a failure
    here must not turn that into an error. Returns how many prompts were cleared.
    """
    try:
        taken = [
            (chat, prompt)
            for chat in bm.chats_for_path(path)
            if (prompt := bm.take_weigh_prompt(chat, day)) is not None
        ]
    except Exception:  # noqa: BLE001
        return 0
    if not taken:
        return 0
    try:
        from navig.telegram.api import _api_call, _load_telegram_token

        token = _load_telegram_token()
    except Exception:  # noqa: BLE001 — no token: state is cleared, the message stays
        return len(taken)
    for chat, (_day, message_id) in taken:
        try:
            _api_call(token, "deleteMessage", {"chat_id": chat, "message_id": message_id})
        except Exception:  # noqa: BLE001 — older than 48 h, or already gone
            pass
    return len(taken)


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
    destination = resolve_user_path(out) if out else path.parent / "out" / f"body-{today}.md"

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
    force: bool = typer.Option(
        False, "--force", help="Send the weigh-in even if the cadence says skip"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Preview — or send to Telegram — the weigh-in prompt or the weekly card.

    Two shapes, because two questions:

      navig body checkin                    # preview the morning weigh-in
      navig body checkin --send             # ask for today's weight
      navig body checkin --weekly --send    # the Sunday trend + check-in card

    Deliver on a schedule by pointing cron jobs at this command:
      navig body checkin --send --space <space>            # 08:10 daily
      navig body checkin --weekly --send --space <space>   # 18:45 Sunday

    The scheduled weigh-in follows `navig body cadence`: never twice a day, not
    when you already weighed within the cadence, only on the chosen weekdays, and
    not after the latest hour (so a daemon restarted at dinner does not ask).
    """
    from navig.telegram import chat_tidy

    chat_tidy.mark_routine("body")  # what this sends is today's; tidied away tomorrow
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
            if as_json:
                ch.emit_json({"ok": True, "sent": False, "skipped": "extension_off"})
                return
            ch.warning(
                "Nothing sent — the Health extension is off.",
                "Turn it on with /extensions in Telegram, or: "
                "navig telegram extensions enable health",
            )
            return

    target: int | None = None
    if send:
        target = chat_id or _default_chat_id()
        if not target:
            if as_json:
                ch.emit_json({"ok": False, "error": "no Telegram chat id"})
            else:
                ch.error("No Telegram chat id.", "Set telegram.allowed_users or pass --chat-id.")
            raise typer.Exit(1)

    # The cadence gate — the SCHEDULED weigh-in only. The weekly card has its own
    # cron line, and the interactive `/weigh` never comes through here.
    if send and not weekly and not force:
        reason = _weigh_skip_reason(int(target or 0), path, when)
        if reason is not None:
            code, text_reason = reason
            if as_json:
                ch.emit_json(
                    {
                        "ok": True,
                        "sent": False,
                        "skipped": code,
                        "reason": text_reason,
                        "date": when,
                    }
                )
            else:
                ch.info(f"Weigh-in skipped: {text_reason}.")
                ch.dim("  Cadence: navig body cadence · send anyway: --force")
            return

    if weekly:
        text, markup = body_actions.build_weekly_card(path, when)
    else:
        text, markup = body_actions.build_weigh_prompt(path, when)

    if not send:
        if as_json:
            ch.emit_json({"ok": True, "sent": False, "text": text, "reply_markup": markup})
            return
        plain = text
        for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
            plain = plain.replace(tag, "")
        ch.console.print(plain)
        for row in markup.get("inline_keyboard", []):
            ch.console.print("  " + "   ".join(b["text"] for b in row))
        ch.dim(f"\n{path}")
        ch.dim("Send it with: navig body checkin --send")
        return

    send_to = int(target or 0)  # resolved (and non-zero) above for every --send
    from navig.telegram.api import _api_call, _load_telegram_token

    # Claimed BEFORE the send, after every slow import: a run the scheduler kills
    # at its timeout once Telegram has accepted the message must still block the
    # retry five minutes later. Measured: a cold start took 64 s against a 60 s
    # timeout, and the retry asked a second time.
    if not weekly:
        bm.mark_weigh_asked(send_to, when)

    try:
        result = _api_call(
            _load_telegram_token(),
            "sendMessage",
            {
                "chat_id": send_to,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": markup,
            },
        )
    except Exception as exc:  # noqa: BLE001
        if not weekly:
            try:
                bm.unmark_weigh_asked(send_to, when)
            except Exception:  # noqa: BLE001
                pass
        if as_json:
            ch.emit_json({"ok": False, "error": f"could not send: {exc}"})
        else:
            ch.error("Could not send the check-in.", str(exc))
        raise typer.Exit(1) from exc

    message_id = (result.get("result") or {}).get("message_id")

    # Sent from HERE, answered by the GATEWAY — a different process whose active
    # space is very likely something else. Record which metrics.csv this chat's
    # answers write into, and which message a typed reply must be a reply to.
    bm.remember_target(send_to, path, message_id=message_id, day=when)
    if not weekly:
        bm.set_prompt(send_to, "weigh", when, message_id)

    if as_json:
        ch.emit_json(
            {
                "ok": True,
                "sent": True,
                "kind": "weekly" if weekly else "weigh",
                "chat_id": send_to,
                "message_id": message_id,
                "date": when,
            }
        )
        return
    ch.success(f"{'Weekly card' if weekly else 'Weigh-in'} sent to {target}.")
    ch.dim(f"{path}")


def _weigh_skip_reason(chat: int, path: Path, when: str) -> tuple[str, str] | None:
    """Apply the cadence rules (``navig.spaces.weigh_cadence``) for one scheduled run."""
    from navig.spaces import weigh_cadence as wc

    day = date.fromisoformat(when)
    try:
        last = bm.last_weighed(path, on_or_before=day)
    except bm.MetricsReadError:
        last = None  # unreadable record: ask rather than go silent
    return wc.skip_reason(
        wc.load_settings(),
        day=day,
        now=_now(),
        last_weighed=last,
        asked_on=bm.weigh_asked_on(chat),
    )


def _fail(message: str, as_json: bool) -> NoReturn:
    if as_json:
        ch.emit_json({"ok": False, "error": message})
    else:
        ch.error(message)
    raise typer.Exit(1)


@body_app.command("cadence")
def body_cadence(
    every: int | None = typer.Option(
        None, "--every", help="Ask at most every N days (1-30; 1 = daily)"
    ),
    weekdays: str | None = typer.Option(
        None,
        "--weekdays",
        "--days",
        help="Only on these days, e.g. mon,thu ('any' clears it)",
    ),
    latest_hour: int | None = typer.Option(
        None,
        "--latest-hour",
        help="Do not ask from this local hour on (1-24; 24 = any time)",
    ),
    enabled: bool | None = typer.Option(
        None, "--on/--off", help="Turn the scheduled weigh-in on or off"
    ),
    space: str | None = typer.Option(None, "--space", help="Space or metrics.csv path"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Telegram chat to report on"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show — or set — how often the scheduled weigh-in asks.

    navig body cadence                                   # what is set, and when next
    navig body cadence --every 3 --latest-hour 12        # every 3 days, before noon
    navig body cadence --days mon,thu                    # only Mondays and Thursdays
    navig body cadence --off                             # stop asking (/weigh still works)
    """
    from navig.spaces import weigh_cadence as wc

    updates: dict[str, object] = {}
    if every is not None:
        if not wc.MIN_EVERY_DAYS <= every <= wc.MAX_EVERY_DAYS:
            _fail(
                f"--every must be between {wc.MIN_EVERY_DAYS} and {wc.MAX_EVERY_DAYS} "
                f"days, got {every}",
                as_json,
            )
        updates["every_days"] = every
    if weekdays is not None:
        try:
            parsed = wc.parse_weekdays(weekdays)
        except ValueError as exc:
            _fail(f"--weekdays: {exc}", as_json)
        updates["weekdays"] = [wc.WEEKDAY_NAMES[i] for i in parsed]
    if latest_hour is not None:
        if not wc.MIN_LATEST_HOUR <= latest_hour <= wc.MAX_LATEST_HOUR:
            _fail(
                f"--latest-hour must be between {wc.MIN_LATEST_HOUR} and "
                f"{wc.MAX_LATEST_HOUR}, got {latest_hour}",
                as_json,
            )
        updates["latest_hour"] = latest_hour
    if enabled is not None:
        updates["enabled"] = enabled

    if updates:
        try:
            wc.save_settings(updates)
        except Exception as exc:  # noqa: BLE001
            _fail(f"could not save the cadence: {exc}", as_json)

    settings = wc.load_settings()
    path = _resolve(space)
    now = _now()
    try:
        last = bm.last_weighed(path, on_or_before=now.date())
    except bm.MetricsReadError:
        last = None
    chat = chat_id or _default_chat_id()
    asked = bm.weigh_asked_on(int(chat)) if chat else None
    nxt = wc.next_eligible(settings, now=now, last_weighed=last, asked_on=asked)

    if as_json:
        ch.emit_json(
            {
                "ok": True,
                "changed": sorted(updates),
                **settings.as_dict(),
                "last_weighed": last.isoformat() if last else None,
                "asked_on": asked,
                "next_eligible": nxt.isoformat() if nxt else None,
            }
        )
        return

    if updates:
        ch.success("Weigh-in cadence saved.")
    table = ch.create_table(
        title="Weigh-in cadence",
        columns=[
            {"name": "Setting", "style": "cyan"},
            {"name": "Value"},
        ],
    )
    table.add_row(
        "Scheduled weigh-in",
        "[green]● on[/green]" if settings.enabled else "[dim]○ off[/dim]",
    )
    table.add_row(
        "Every",
        "day" if settings.every_days == 1 else f"{settings.every_days} days",
    )
    table.add_row("Weekdays", ", ".join(settings.weekday_names()) or "any")
    table.add_row(
        "Ask before",
        "any time" if settings.latest_hour >= 24 else f"{settings.latest_hour:02d}:00",
    )
    table.add_row("Last weighed", last.isoformat() if last else "[dim]—[/dim]")
    table.add_row("Last asked", asked or "[dim]—[/dim]")
    table.add_row(
        "Next eligible",
        nxt.isoformat() if nxt else "[dim]— (off)[/dim]",
    )
    ch.print_table(table)
    ch.dim(
        "Change it: navig body cadence --every 3 --days mon,thu --latest-hour 12 "
        "· /weigh in Telegram always works"
    )


def _default_chat_id() -> int | None:
    """First allowed Telegram user, the same rule `navig habit` uses."""
    try:
        from navig.config import get_config_manager

        allowed = get_config_manager().get("telegram.allowed_users", default=[]) or []
        return int(allowed[0]) if allowed else None
    except Exception:  # noqa: BLE001
        return None
