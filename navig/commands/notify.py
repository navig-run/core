"""``navig notify`` — fire a typed notification through the notify router.

Why this exists
---------------
Everything that reaches the operator already routes through
:mod:`navig.notify.router`, which resolves channels **per notification type** from
the preference matrix. But nothing on the CLI could dispatch one, so a scheduled
job had exactly two options: call a single transport directly
(``navig telegram send …``, which pins it to one channel forever) or go without.

That is how the operator ended up with the routing inverted — routine habit nudges
arriving by SMS because they share the ``reminder`` type, while real appointments
sent with ``navig telegram send`` could never reach SMS at all, however urgent.

A cron line can now say what a message *is* and let the matrix decide where it
goes:

    navig notify send appointment "Blood test 07:00 — fasting" \\
        --body "Bring Dr Leon's prescription"

Change your mind about channels later and you change the matrix, not the crontab.
"""

from __future__ import annotations

import asyncio

import typer

from navig import console_helper as ch

notify_app = typer.Typer(
    name="notify",
    help="Send a typed notification; inspect how each type is routed",
    no_args_is_help=True,
)


@notify_app.command("send")
def send(
    type_key: str = typer.Argument(..., help="Notification type (see `navig notify types`)"),
    title: str = typer.Argument(..., help="One-line headline"),
    body: str = typer.Option("", "--body", "-b", help="Optional detail below the headline"),
    json_out: bool = typer.Option(False, "--json", help="Print the delivery result as JSON"),
) -> None:
    """Dispatch one notification and report which channels accepted it."""
    from navig.notify.router import dispatch
    from navig.notify.types import TYPE_KEYS

    if type_key not in TYPE_KEYS:
        # Fail here rather than let the router's unknown-type fallback quietly
        # deliver it deck-only: from a cron that looks like nothing happened.
        ch.error(
            f"Unknown notification type {type_key!r}.",
            "Run `navig notify types` to see the registered ones.",
        )
        raise typer.Exit(1)

    outcome = asyncio.run(dispatch(type_key, title, body))
    channels = (outcome or {}).get("channels") or []

    if json_out:
        import json  # noqa: PLC0415

        ch.console.print_json(json.dumps(outcome or {}))
        return

    if not channels:
        # Not an error: a type the operator muted everywhere legitimately reaches
        # nothing. Say so plainly instead of implying a send happened.
        ch.warning(
            f"{type_key}: delivered to no channel.",
            "Every channel for this type is off, or quiet hours are active.",
        )
        return

    delivered = [c.get("name", "?") for c in channels if c.get("ok")]
    failed = [c.get("name", "?") for c in channels if not c.get("ok")]

    if delivered:
        ch.success(f"{type_key} → {', '.join(delivered)}")
    if failed:
        # Exit non-zero only when NOTHING landed — a partial send is still a send,
        # and a cron that retries on partial success would double-notify.
        ch.warning(f"failed: {', '.join(failed)}")
        if not delivered:
            raise typer.Exit(1)


@notify_app.command("types")
def types(
    json_out: bool = typer.Option(False, "--json", help="Print the routing table as JSON"),
) -> None:
    """Show every notification type and the channels it currently routes to."""
    from navig.notify import prefs
    from navig.notify.types import NOTIFICATION_TYPES

    rows = [
        {
            "key": t["key"],
            "label": t.get("label", ""),
            "category": t.get("category", ""),
            "channels": prefs.enabled_channels(t["key"]),
        }
        for t in NOTIFICATION_TYPES
    ]

    if json_out:
        import json  # noqa: PLC0415

        ch.console.print_json(json.dumps(rows))
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Type", no_wrap=True)
    table.add_column("Category", no_wrap=True)
    # The one free-text column, so a narrow terminal degrades here rather than
    # truncating the type names that make the table useful.
    table.add_column("Routes to")

    for r in rows:
        chans = r["channels"]
        rendered = (
            "[dim]○ nowhere[/dim]"
            if not chans
            else " · ".join(
                f"[green]{c}[/green]" if c == "sms" else c for c in chans
            )
        )
        table.add_row(r["key"], r["category"], rendered)

    ch.console.print(table)
    smsy = [r["key"] for r in rows if "sms" in r["channels"]]
    ch.dim(
        f"{len(smsy)}/{len(rows)} type(s) reach SMS ({', '.join(smsy) or 'none'}) · "
        f"send one with navig notify send <type> \"…\""
    )
