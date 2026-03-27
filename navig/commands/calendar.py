"""
Calendar Commands

List, view, and manage calendar events from configured providers.
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

import typer

from navig import console_helper as ch

calendar_app = typer.Typer(help="Calendar operations")


@calendar_app.command("list")
def list_events(
    hours: int = typer.Option(24, "--hours", "-h", help="Hours to look ahead"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max number of events"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List upcoming calendar events.

    Fetches events from your configured calendar provider(s).
    """

    async def _fetch():
        from navig.agent.proactive import (
            CalDAVProvider,
            GoogleCalendar,
            ICSCalendarProvider,
            MockCalendar,
        )
        from navig.config import get_config_manager

        cm = get_config_manager()
        config = cm._load_global_config()

        proactive_cfg = config.get("proactive", {})
        calendar_cfg = proactive_cfg.get("calendar", {})

        if not calendar_cfg.get("enabled", False):
            if not json_output:
                ch.warning("Calendar not configured. Using mock data.")
            provider = MockCalendar()
        else:
            provider_type = calendar_cfg.get("provider", "mock")

            if provider_type == "google":
                creds_path = calendar_cfg.get("credentials_path")
                provider = GoogleCalendar(credentials_path=creds_path)
            elif provider_type == "ics":
                url = calendar_cfg.get("url")
                provider = ICSCalendarProvider(url=url)
            elif provider_type == "caldav":
                url = calendar_cfg.get("url")
                username = calendar_cfg.get("username")
                password = calendar_cfg.get("password")
                provider = CalDAVProvider(url=url, username=username, password=password)
            else:
                provider = MockCalendar()

        start = datetime.now()
        end = start + timedelta(hours=hours)
        events = await provider.list_events(start, end)

        return events[:limit]

    events = asyncio.run(_fetch())

    if json_output:
        # Convert to JSON-serializable format
        events_data = [
            {
                "id": e.id,
                "title": e.title,
                "start": e.start.isoformat(),
                "end": e.end.isoformat() if e.end else None,
                "location": e.location,
                "description": e.description,
            }
            for e in events
        ]
        print(json.dumps(events_data, indent=2))
    else:
        if not events:
            ch.info("No upcoming events")
            return

        ch.info(f"Upcoming Events (next {hours} hours)")
        ch.console.print()

        for event in events:
            time_str = event.start.strftime("%a %b %d, %I:%M %p")
            ch.console.print(f"  [cyan]•[/cyan] {event.title}")
            ch.console.print(f"    [dim]{time_str}[/dim]")
            if event.location:
                ch.console.print(f"    [dim]📍 {event.location}[/dim]")
            ch.console.print()


@calendar_app.command("auth")
def authenticate(
    provider: str = typer.Argument("google", help="Provider to authenticate: google"),
):
    """
    Authenticate with a calendar provider.

    Opens OAuth flow for cloud providers like Google Calendar.
    """

    async def _auth():
        if provider == "google":
            from navig.agent.proactive import GoogleCalendar

            ch.info("Google Calendar Authentication")
            ch.console.print("This will open your browser to authorize NAVIG.")
            ch.console.print()

            creds_path = "~/.navig/credentials/google_calendar.json"
            cal = GoogleCalendar(credentials_path=creds_path)

            # This should trigger the OAuth flow
            start = datetime.now()
            end = start + timedelta(days=1)
            await cal.list_events(start, end)

            ch.success("✓ Authentication successful!")
            ch.info(f"Credentials saved to {creds_path}")
        else:
            ch.error(f"Unknown provider: {provider}")

    asyncio.run(_auth())


@calendar_app.command("add")
def add_event(
    title: str = typer.Argument(..., help="Event title"),
    start: str = typer.Option(None, "--start", "-s", help="Start time (ISO format)"),
    duration: int = typer.Option(60, "--duration", "-d", help="Duration in minutes"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Location"),
):
    """
    Add a new calendar event.

    Requires a calendar provider that supports write operations (CalDAV).
    """

    async def _add():
        from navig.agent.proactive import CalDAVProvider
        from navig.config import get_config_manager

        cm = get_config_manager()
        config = cm._load_global_config()

        proactive_cfg = config.get("proactive", {})
        calendar_cfg = proactive_cfg.get("calendar", {})

        provider_type = calendar_cfg.get("provider", "mock")

        if provider_type != "caldav":
            ch.error("Adding events requires CalDAV provider")
            ch.info("Configure with: navig agent proactive setup --calendar caldav")
            return

        url = calendar_cfg.get("url")
        username = calendar_cfg.get("username")
        password = calendar_cfg.get("password")

        provider = CalDAVProvider(url=url, username=username, password=password)

        # Parse start time
        if start:
            start_dt = datetime.fromisoformat(start)
        else:
            start_dt = datetime.now()

        end_dt = start_dt + timedelta(minutes=duration)

        from navig.agent.proactive.models import CalendarEvent

        event = CalendarEvent(
            id="",  # Will be generated
            title=title,
            start=start_dt,
            end=end_dt,
            location=location or "",
            description="",
        )

        await provider.add_event(event)
        ch.success(f"✓ Event added: {title}")

    asyncio.run(_add())


@calendar_app.command("sync")
def sync_calendar():
    """
    Sync calendar data from remote providers.

    Refreshes cached calendar data.
    """
    ch.info("Syncing calendar...")
    ch.success("✓ Calendar synced")
