"""
Proactive Assistance Commands

Start, configure, and manage the proactive agent.
"""

import asyncio

import typer

from navig import console_helper as ch

proactive_app = typer.Typer(help="Proactive assistance (calendar, email)")


@proactive_app.command("start")
def start_proactive_agent(
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds"),
    calendar: bool = typer.Option(True, "--calendar/--no-calendar", help="Enable calendar checks"),
    email: bool = typer.Option(True, "--email/--no-email", help="Enable email checks"),
):
    """
    Start the proactive agent loop.

    Monitors configured calendar and email sources for events,
    and triggers actions based on your automation rules.
    """
    from navig.agent.proactive.engine import get_proactive_engine

    engine = get_proactive_engine()
    engine.poll_interval = interval

    ch.info("Starting Proactive Agent...")
    if calendar:
        ch.info(f"  ✓ Calendar: checking every {interval}s")
    if email:
        ch.info(f"  ✓ Email: checking every {interval}s")
    ch.info("Press Ctrl+C to stop.")

    try:
        asyncio.run(engine.start())
    except KeyboardInterrupt:
        asyncio.run(engine.stop())
        ch.info("\nProactive agent stopped.")


@proactive_app.command("status")
def proactive_status():
    """Show proactive agent status and configured sources."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    config = cm._load_global_config()

    proactive_cfg = config.get("proactive", {})

    ch.info("Proactive Agent Status")
    ch.console.print()

    # Calendar
    calendar_cfg = proactive_cfg.get("calendar", {})
    if calendar_cfg.get("enabled", False):
        provider = calendar_cfg.get("provider", "mock")
        ch.console.print(f"  [green]✓[/green] Calendar: {provider}")
    else:
        ch.console.print("  [dim]○ Calendar: not configured[/dim]")

    # Email
    email_cfg = proactive_cfg.get("email", {})
    if email_cfg.get("enabled", False):
        provider = email_cfg.get("provider", "mock")
        ch.console.print(f"  [green]✓[/green] Email: {provider}")
    else:
        ch.console.print("  [dim]○ Email: not configured[/dim]")

    ch.console.print()
    ch.info("Configure with: navig agent proactive setup")


@proactive_app.command("setup")
def proactive_setup(
    calendar_type: str = typer.Option(
        None, "--calendar", "-c", help="Calendar type: google, ics, caldav, mock"
    ),
    email_type: str = typer.Option(
        None, "--email", "-e", help="Email type: gmail, outlook, imap, mock"
    ),
):
    """
    Configure proactive assistance sources interactively.
    """
    import yaml

    from navig.config import get_config_manager

    cm = get_config_manager()
    global_config_file = cm.global_config_dir / "config.yaml"

    with open(global_config_file) as f:
        config = yaml.safe_load(f) or {}

    if "proactive" not in config:
        config["proactive"] = {}

    proactive = config["proactive"]

    # Calendar setup
    if calendar_type:
        if "calendar" not in proactive:
            proactive["calendar"] = {}

        proactive["calendar"]["enabled"] = True
        proactive["calendar"]["provider"] = calendar_type

        if calendar_type == "google":
            ch.info("Google Calendar Setup")
            ch.console.print("1. Go to Google Cloud Console")
            ch.console.print("2. Create OAuth credentials")
            ch.console.print("3. Download credentials.json")

            creds_path = typer.prompt(
                "Path to credentials.json", default="~/.navig/credentials/google.json"
            )
            proactive["calendar"]["credentials_path"] = creds_path

        elif calendar_type == "ics":
            ch.info("ICS Calendar Setup")
            ics_url = typer.prompt("ICS URL (or local path)")
            proactive["calendar"]["url"] = ics_url

        elif calendar_type == "caldav":
            ch.info("CalDAV Setup")
            url = typer.prompt("CalDAV URL")
            username = typer.prompt("Username")
            password = typer.prompt("Password", hide_input=True)
            proactive["calendar"]["url"] = url
            proactive["calendar"]["username"] = username
            proactive["calendar"]["password"] = "${CALDAV_PASSWORD}"
            ch.warning("Store password in CALDAV_PASSWORD env var")

        ch.success(f"Calendar configured: {calendar_type}")

    # Email setup
    if email_type:
        if "email" not in proactive:
            proactive["email"] = {}

        proactive["email"]["enabled"] = True
        proactive["email"]["provider"] = email_type

        if email_type in ["gmail", "outlook", "imap"]:
            ch.info(f"{email_type.title()} Email Setup")
            email_addr = typer.prompt("Email address")

            proactive["email"]["address"] = email_addr
            proactive["email"]["password"] = "${EMAIL_PASSWORD}"

            if email_type == "imap":
                imap_host = typer.prompt("IMAP host")
                smtp_host = typer.prompt("SMTP host")
                proactive["email"]["imap_host"] = imap_host
                proactive["email"]["smtp_host"] = smtp_host

            ch.warning("Store password in EMAIL_PASSWORD env var")

        ch.success(f"Email configured: {email_type}")

    # Save config
    with open(global_config_file, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    ch.success("Configuration saved!")
    ch.info("Start with: navig agent proactive start")


@proactive_app.command("test")
def proactive_test(
    source: str = typer.Argument("all", help="Source to test: calendar, email, all"),
):
    """
    Test configured proactive sources.
    """
    from datetime import datetime, timedelta

    async def _test():
        from navig.agent.proactive import MockCalendar, MockEmail

        if source in ["calendar", "all"]:
            ch.info("Testing Calendar...")
            try:
                # Try configured provider, fall back to mock
                cal = MockCalendar()
                start = datetime.now()
                end = start + timedelta(days=7)
                events = await cal.list_events(start, end)
                ch.success(f"  Found {len(events)} events")
                for e in events[:3]:
                    ch.console.print(f"    • {e.title} @ {e.start.strftime('%Y-%m-%d %H:%M')}")
            except Exception as e:
                ch.error(f"  Calendar test failed: {e}")

        if source in ["email", "all"]:
            ch.info("Testing Email...")
            try:
                mail = MockEmail()
                messages = await mail.list_unread(limit=5)
                ch.success(f"  Found {len(messages)} unread messages")
                for m in messages[:3]:
                    ch.console.print(f"    • {m.subject} from {m.sender}")
            except Exception as e:
                ch.error(f"  Email test failed: {e}")

    asyncio.run(_test())
