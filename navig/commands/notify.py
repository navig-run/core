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

    # Duplicate-send guard. A scheduled daily card is keyed per DAY (one briefing a day,
    # whatever the body says), so a retry loop or a caller running twice cannot flood the
    # operator — the 2026-10-22 incident sent the same briefing 12 times in 25 minutes.
    # Other types are keyed by content for a short cooldown, which absorbs retries while
    # still letting a genuinely different message through.
    import hashlib  # noqa: PLC0415
    import time  # noqa: PLC0415

    from navig.notify import dedupe  # noqa: PLC0415

    daily = {"briefing", "digest", "checkin", "review"}
    if type_key in daily:
        day = time.strftime("%Y-%m-%d")
        dedupe_key = f"send:{type_key}:{day}"
        window_s = 20 * 3600
    else:
        digest = hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()[:16]
        dedupe_key = f"send:{type_key}:{digest}"
        window_s = 15 * 60

    if not dedupe.claim(dedupe_key, window_s):
        if json_out:
            import json  # noqa: PLC0415

            ch.console.print_json(
                json.dumps({"skipped": "duplicate", "type": type_key, "key": dedupe_key})
            )
        else:
            ch.info(f"{type_key}: already delivered — duplicate send suppressed.")
        return

    outcome = asyncio.run(dispatch(type_key, title, body))
    channels = (outcome or {}).get("channels") or []

    if json_out:
        import json  # noqa: PLC0415

        ch.console.print_json(json.dumps(outcome or {}))
        return

    if not channels:
        # Nothing reached a channel (muted everywhere / quiet hours): give the claim
        # back, or a legitimate later send would be suppressed for the whole window.
        dedupe.release(dedupe_key)
        ch.warning(
            f"{type_key}: delivered to no channel.",
            "Every channel for this type is off, or quiet hours are active.",
        )
        return

    # The router's per-channel result is {"channel", "ok", "detail"} — there is no
    # "name" key, which is how every channel used to print as "?".
    delivered = [c.get("channel", "?") for c in channels if c.get("ok")]
    failed = [f"{c.get('channel', '?')} ({c.get('detail', '')})" for c in channels if not c.get("ok")]

    if delivered:
        ch.success(f"{type_key} → {', '.join(delivered)}")
    if failed:
        # Exit non-zero only when NOTHING landed — a partial send is still a send,
        # and a cron that retries on partial success would double-notify.
        ch.warning(f"failed: {', '.join(failed)}")
        if not delivered:
            dedupe.release(dedupe_key)
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


route_app = typer.Typer(
    name="route",
    help="Send a notification type to its own Telegram chat, and set whether it rings",
    no_args_is_help=True,
)
notify_app.add_typer(route_app, name="route")


@route_app.command("list")
def route_list(
    json_out: bool = typer.Option(False, "--json", help="Print every type's route as JSON"),
) -> None:
    """Show where each type lands on Telegram and whether it rings."""
    from navig.notify import prefs, telegram_routes

    routes = telegram_routes.all_routes()
    if json_out:
        import json  # noqa: PLC0415

        ch.console.print_json(json.dumps(routes))
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Type", no_wrap=True)
    table.add_column("Sound", no_wrap=True)
    table.add_column("Telegram chat")
    routed = 0
    for key, r in routes.items():
        tg_on = prefs.is_enabled(key, "telegram")
        if r["chat_id"] is None and not r["silent"] and not tg_on:
            continue  # never reaches Telegram and has no setting — noise
        if r["chat_id"] is not None:
            routed += 1
            where = f"[green]{r['title'] or r['chat_id']}[/green] [dim]{r['chat_id']}[/dim]"
        else:
            where = "[dim]your DM[/dim]"
        if not tg_on:
            where += " [yellow](Telegram off for this type)[/yellow]"
        sound = "[dim]🔕 silent[/dim]" if r["silent"] else "🔔 rings"
        table.add_row(key, sound, where)
    ch.console.print(table)
    ch.dim(f"{routed} type(s) have their own chat · route one with navig notify route set <type> <chat>")


# ignore_unknown_options: every Telegram channel/supergroup id is NEGATIVE, and
# Click reads a leading "-" as an option — `route set sale -100…` died with
# "No such option: -1", i.e. the command could not take the ids its own help
# documents. Same trap and fix as `deletions target` / `deletions mute`.
@route_app.command("set", context_settings={"ignore_unknown_options": True})
def route_set(
    type_key: str = typer.Argument(..., help="Notification type (see `navig notify types`)"),
    chat: str = typer.Argument(..., help="Channel/group id (e.g. 4466964318), -100… id, or @username"),
) -> None:
    """Route a type to a chat. The bot must already be in it (an admin, for a channel)."""
    from navig.notify import telegram_routes

    try:
        resolved = telegram_routes.resolve_chat(chat)
        telegram_routes.set_route(type_key, resolved.chat_id, resolved.title)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    ch.success(f"{type_key} → {resolved.title or resolved.chat_id} ({resolved.kind}, {resolved.chat_id})")
    ch.dim(f"Check it with: navig notify route test {type_key}")


@route_app.command("clear")
def route_clear(
    type_key: str = typer.Argument(..., help="Notification type to send back to your DM"),
) -> None:
    """Send a type back to your DM."""
    from navig.notify import telegram_routes

    try:
        telegram_routes.set_route(type_key, None)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    ch.success(f"{type_key} → your DM")


@route_app.command("sound")
def route_sound(
    type_key: str = typer.Argument(..., help="Notification type"),
    mode: str = typer.Argument(..., help="on (rings) · off (silent) · default"),
) -> None:
    """Choose whether a type rings or lands silently on Telegram."""
    from navig.notify import telegram_routes

    value = {"on": False, "off": True, "default": None}.get(mode.strip().lower(), "bad")
    if value == "bad":
        ch.error(f"Unknown sound mode {mode!r}.", "Use: on · off · default")
        raise typer.Exit(1)
    try:
        telegram_routes.set_silent(type_key, value)  # type: ignore[arg-type]
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    state = "🔕 silent" if telegram_routes.is_silent(type_key) else "🔔 rings"
    ch.success(f"{type_key}: {state}")


@route_app.command("test")
def route_test(
    type_key: str = typer.Argument(..., help="Notification type to test"),
) -> None:
    """Post a test message where this type is routed now."""
    from navig.notify import prefs, telegram_routes
    from navig.notify.router import format_telegram_html
    from navig.notify.types import NOTIFICATION_TYPES

    if not prefs._is_known_type(type_key):
        ch.error(f"Unknown notification type {type_key!r}.", "Run `navig notify types`.")
        raise typer.Exit(1)
    label = next((t["label"] for t in NOTIFICATION_TYPES if t["key"] == type_key), type_key)
    text = format_telegram_html(
        type_key, f"Test — {label}", f"{label} notifications will arrive here."
    )
    ok, detail = telegram_routes.deliver(
        type_key, text, fallback_chat_id=telegram_routes.default_chat_id()
    )
    if ok:
        ch.success(f"{type_key}: {detail}")
    else:
        ch.error(f"{type_key}: {detail}")
        raise typer.Exit(1)


pushover_app = typer.Typer(
    name="pushover",
    help="Pushover: alerts that ring on silent and repeat until you acknowledge",
    no_args_is_help=True,
)
notify_app.add_typer(pushover_app, name="pushover")


@pushover_app.command("setup")
def pushover_setup(
    user_key: str = typer.Option("", "--user", help="Your User Key (pushover.net dashboard)"),
    app_token: str = typer.Option("", "--token", help="API token of your NAVIG app (pushover.net/apps/build)"),
) -> None:
    """Validate and save your Pushover keys (to the vault). Prompts for any not given."""
    from navig.notify import prefs, pushover

    if not user_key:
        user_key = typer.prompt("User Key (pushover.net, top of the dashboard)").strip()
    if not app_token:
        app_token = typer.prompt("App API token (pushover.net/apps/build)", hide_input=True).strip()
    out = pushover.validate(app_token, user_key)
    if not out.ok:
        ch.error(f"Pushover rejected those keys: {out.detail}")
        raise typer.Exit(1)
    first_time = not pushover.is_configured()
    try:
        pushover.save_credentials(app_token, user_key)
    except RuntimeError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    ch.success(f"Pushover connected — {out.detail} ({', '.join(out.devices) or 'all devices'})")
    if first_time:
        for t in pushover.RECOMMENDED_TYPES:
            prefs.set_cell(t, "pushover", True)
        ch.dim(f"Turned Pushover on for: {', '.join(pushover.RECOMMENDED_TYPES)}")
    ch.dim("Try an emergency alert: navig notify pushover test --emergency")


@pushover_app.command("test")
def pushover_test(
    emergency: bool = typer.Option(
        False, "--emergency", help="Send at emergency priority — rings on silent, repeats until acknowledged"
    ),
) -> None:
    """Send a test message to your phone."""
    from navig.notify import pushover

    out = pushover.send(
        "NAVIG test",
        "Emergency test — tap Acknowledge to stop it repeating." if emergency else "Pushover is connected.",
        priority=2 if emergency else 0,
        sound_name=pushover.sound(),
    )
    if out.ok:
        ch.success(f"pushover: {out.detail}")
    else:
        ch.error(f"pushover: {out.detail}")
        raise typer.Exit(1)


@pushover_app.command("status")
def pushover_status(
    json_out: bool = typer.Option(False, "--json", help="Print the status as JSON"),
) -> None:
    """Show whether Pushover is connected, which types use it, and the emergency rule."""
    from navig.notify import call as voice
    from navig.notify import prefs, pushover
    from navig.notify.types import TYPE_KEYS

    types_on = [t for t in TYPE_KEYS if prefs.is_enabled(t, "pushover")]
    data = {
        "configured": pushover.is_configured(),
        "emergency": pushover.emergency_level(),
        "sound": pushover.sound(),
        "types": types_on,
        "escalate_min": pushover.escalate_after_min(),
        "sms_target_set": bool(prefs.get_target("sms")),
        # Voice escalation (off unless the operator opted in — it costs money).
        "call_min": voice.call_after_min(),
        "voice_ready": list(voice.is_ready()),
    }
    if json_out:
        import json  # noqa: PLC0415

        ch.console.print_json(json.dumps(data))
        return
    if data["configured"]:
        ch.success("Pushover connected")
    else:
        ch.warning("Pushover not connected", "Set it up with: navig notify pushover setup")
    rule = {
        "high": "high + critical priority repeat until acknowledged",
        "critical": "only critical priority repeats until acknowledged",
        "never": "nothing repeats (plain push only)",
    }[data["emergency"]]
    ch.dim(f"Emergency: {rule}  ·  change with: navig notify pushover emergency high|critical|never")
    ch.dim(f"Types using Pushover: {', '.join(types_on) or 'none'}  ·  sound: {data['sound'] or 'device default'}")
    minutes = data["escalate_min"]
    ch.dim(
        f"SMS escalation: after {minutes} min unacknowledged"
        if minutes
        else "SMS escalation: off"
    )
    ch.dim("change with: navig notify pushover escalate <minutes>  (0 = off)")
    call_min, (voice_ok, voice_why) = data["call_min"], tuple(data["voice_ready"])
    if call_min is None:
        ch.dim("Voice call: off — turn on with: navig notify pushover call <minutes>")
    elif voice_ok:
        ch.dim(
            f"Voice call: after {call_min} min unacknowledged · from {voice.from_number()} "
            f"to {prefs.get_target('sms')}"
        )
    else:
        ch.warning(f"Voice call is on but cannot dial: {voice_why}")


@pushover_app.command("escalate")
def pushover_escalate(
    minutes: int = typer.Argument(..., help="Minutes before an unacknowledged emergency is texted (0 = never)"),
) -> None:
    """Choose when an unacknowledged emergency is escalated to SMS."""
    from navig.notify import prefs

    if minutes < 0 or minutes > 180:
        ch.error("Minutes must be between 0 and 180.", "Pushover stops repeating after 60 minutes.")
        raise typer.Exit(1)
    prefs.set_setting("pushover_escalate_min", minutes)
    if minutes and not prefs.get_target("sms"):
        ch.warning(
            f"Escalation set to {minutes} min — but no SMS number is configured, so it cannot send.",
            "Set one in Mini App → Notifications → Channels → SMS.",
        )
        return
    ch.success(f"SMS escalation: {'after ' + str(minutes) + ' min' if minutes else 'off'}")


@pushover_app.command("emergency")
def pushover_emergency(
    level: str = typer.Argument(..., help="high (high + critical) · critical · never"),
) -> None:
    """Choose which priorities ring on silent and repeat until acknowledged."""
    from navig.notify import prefs, pushover

    level = level.strip().lower()
    if level not in pushover.EMERGENCY_LEVELS:
        ch.error(f"Unknown level {level!r}.", "Use: high · critical · never")
        raise typer.Exit(1)
    prefs.set_setting("pushover_emergency", level)
    ch.success(f"pushover emergency: {level}")


@pushover_app.command("sound")
def pushover_sound(
    name: str = typer.Argument(..., help="A Pushover sound (siren, persistent, cashregister…) or 'default'"),
) -> None:
    """Pick the Pushover sound NAVIG asks for ('default' = your device setting)."""
    from navig.notify import prefs

    value = "" if name.strip().lower() == "default" else name.strip()
    prefs.set_setting("pushover_sound", value)
    ch.success(f"pushover sound: {value or 'device default'}")


@pushover_app.command("call")
def pushover_call(
    minutes: str = typer.Argument(
        ...,
        help="Minutes after an unacknowledged emergency before NAVIG TELEPHONES you "
        "(0 = as soon as it escalates) · 'off' disables calls",
    ),
) -> None:
    """Telephone you when a Pushover emergency goes unacknowledged.

    Calls spend money per attempt (Twilio), so they are off until you turn them
    on here. The SMS escalation stays independent: whichever window lands first
    wins, and a call supersedes a later SMS.
    """
    from navig.notify import call as voice
    from navig.notify import prefs

    word = minutes.strip().lower()
    if word in ("off", "none", "disable", "no"):
        prefs.set_setting("pushover_call_min", "")
        ch.success("Voice call: off")
        return
    if word == "status":
        ready, why = voice.is_ready()
        ch.success(f"Voice call: {'ready' if ready else 'not ready — ' + why}")
        return
    try:
        n = int(word)
    except ValueError:
        ch.error(f"Unknown value {minutes!r}.", "Use minutes (0–180) or 'off'.")
        raise typer.Exit(1) from None
    if n < 0 or n > 180:
        ch.error("Minutes must be between 0 and 180.", "Pushover stops repeating after 60 minutes.")
        raise typer.Exit(1)
    prefs.set_setting("pushover_call_min", n)
    ready, why = voice.is_ready()
    if not ready:
        ch.warning(
            f"Voice call set to {n} min — but it cannot dial yet: {why}",
            "Fix that, or turn calls off again with: navig notify pushover call off",
        )
        return
    ch.success(f"Voice call: after {n} min unacknowledged · {voice.from_number()} → {prefs.get_target('sms')}")
    ch.dim("Costs money per attempt (Twilio). Emergency keeps ringing on Pushover throughout.")
