"""
ICS/CalDAV Calendar Provider

Supports:
- Local .ics files
- Remote ICS URLs (Nextcloud, Fastmail, iCloud, etc.)
- CalDAV servers

No OAuth required - works with any standard ICS feed.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from navig.agent.proactive.providers import CalendarEvent, CalendarProvider

logger = logging.getLogger(__name__)


def _as_aware(dt: datetime) -> datetime:
    """Return *dt* tz-aware so naive and tz-aware calendar times can be compared and
    sorted without ``TypeError``. A naive value is interpreted as local time — the
    convention for floating iCalendar times, and matching the naive ``datetime.now()``
    bounds the CLI passes in."""
    return dt if dt.tzinfo is not None else dt.astimezone()


class ICSCalendarProvider(CalendarProvider):
    """
    ICS calendar provider for self-hosted calendars.

    Usage:
        # From URL (e.g., Nextcloud public link)
        provider = ICSCalendarProvider(url="https://cloud.example.com/calendar.ics")

        # From local file
        provider = ICSCalendarProvider(path=Path("~/calendar.ics"))
    """

    def __init__(
        self,
        url: str | None = None,
        path: Path | None = None,
        cache_minutes: int = 5,
    ):
        """
        Initialize ICS provider.

        Args:
            url: Remote ICS URL
            path: Local .ics file path
            cache_minutes: How long to cache fetched data
        """
        if not url and not path:
            raise ValueError("Either 'url' or 'path' must be provided")

        self.url = url
        self.path = Path(path).expanduser() if path else None
        self.cache_minutes = cache_minutes
        self._cache: list[CalendarEvent] | None = None
        self._cache_time: datetime | None = None

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """
        Fetch events from ICS source within the given time range.
        """
        try:
            from icalendar import Calendar
        except ImportError as _exc:
            raise ImportError("ICS support requires: pip install icalendar") from _exc

        # Check cache
        if self._cache and self._cache_time:
            if datetime.now() - self._cache_time < timedelta(minutes=self.cache_minutes):
                return self._filter_events(self._cache, start, end)

        ics_data = await self._fetch_ics()
        if not ics_data:
            return []

        cal = Calendar.from_ical(ics_data)
        events = []

        for component in cal.walk():
            if component.name == "VEVENT":
                evt = self._parse_vevent(component)
                if evt:
                    events.append(evt)

        # Update cache
        self._cache = events
        self._cache_time = datetime.now()

        return self._filter_events(events, start, end)

    async def create_event(self, event: CalendarEvent) -> str:
        """ICS files are read-only."""
        raise NotImplementedError(
            "ICS provider is read-only. Use Google Calendar or CalDAV for write access."
        )

    async def _fetch_ics(self) -> str | None:
        """Fetch ICS data from URL or file."""
        if self.url:
            # SSRF: the URL is operator-configured but fetched with redirects, so
            # a legit-looking feed that 302s to an internal address (cloud
            # metadata, the local daemon, a LAN host) must be re-checked. Go
            # through safe_fetch — it validates the initial URL AND every redirect
            # hop (a bare check_url would only cover the first). Secure by default;
            # local calendars are enabled with net.ssrf.allow_private_network.
            from navig.net.ssrf import SsrfBlockedError, policy_from_config, safe_fetch

            try:
                resp = await safe_fetch(self.url, policy_from_config())
            except SsrfBlockedError as exc:
                logger.warning("ICS calendar fetch blocked by SSRF policy: %s", exc)
                return None
            except ValueError as exc:
                # malformed URL, non-http scheme, or redirect chain too long
                logger.warning("ICS calendar fetch rejected: %s", exc)
                return None
            if resp.status_code == 200:
                return resp.text
            return None

        elif self.path and self.path.exists():
            return self.path.read_text(encoding="utf-8")

        return None

    def _parse_vevent(self, component) -> CalendarEvent | None:
        """Parse an iCalendar VEVENT component."""
        try:
            evt_start = component.get("dtstart")
            evt_end = component.get("dtend")

            if not evt_start:
                return None

            start_dt = self._to_datetime(evt_start.dt)

            if evt_end:
                end_dt = self._to_datetime(evt_end.dt)
            else:
                # Default to 1 hour duration
                end_dt = start_dt + timedelta(hours=1)

            return CalendarEvent(
                id=str(component.get("uid", "")),
                title=str(component.get("summary", "Untitled")),
                start=start_dt,
                end=end_dt,
                location=str(component.get("location", "")) or None,
                description=str(component.get("description", "")) or None,
                attendees=self._parse_attendees(component),
            )
        except Exception:
            return None

    def _to_datetime(self, dt) -> datetime:
        """Convert iCalendar date/datetime to Python datetime."""
        if isinstance(dt, datetime):
            return dt
        # It's a date, convert to datetime at midnight
        return datetime.combine(dt, datetime.min.time())

    def _parse_attendees(self, component) -> list[str]:
        """Extract attendee emails from VEVENT."""
        attendees = []
        for attendee in component.get("attendee", []):
            if hasattr(attendee, "__str__"):
                email = str(attendee).replace("mailto:", "")
                attendees.append(email)
        return attendees

    def _filter_events(
        self, events: list[CalendarEvent], start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        """Filter events to those within the time range.

        tz-safe: a tz-aware ``DTSTART`` (``…Z`` / ``TZID``) used to raise
        ``TypeError: can't compare offset-naive and offset-aware datetimes`` against the
        naive ``datetime.now()`` bounds. Both sides are coerced to aware for the compare.
        """
        start_a, end_a = _as_aware(start), _as_aware(end)
        return [e for e in events if start_a <= _as_aware(e.start) <= end_a]


class CalDAVProvider(CalendarProvider):
    """
    CalDAV calendar provider for write-capable self-hosted calendars.

    Supports Nextcloud, Radicale, Baikal, etc.
    """

    def __init__(self, url: str, username: str, password: str):
        """
        Initialize CalDAV provider.

        Args:
            url: CalDAV server URL (e.g., https://cloud.example.com/remote.php/dav)
            username: CalDAV username
            password: CalDAV password
        """
        self.url = url
        self.username = username
        self.password = password

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """List events from CalDAV server."""
        try:
            import caldav
        except ImportError as _exc:
            raise ImportError("CalDAV support requires: pip install caldav") from _exc

        client = caldav.DAVClient(url=self.url, username=self.username, password=self.password)

        principal = client.principal()
        calendars = principal.calendars()

        if not calendars:
            return []

        # Use first calendar
        calendar = calendars[0]
        raw_events = calendar.date_search(start=start, end=end)

        events = []
        for raw in raw_events:
            try:
                from icalendar import Calendar

                cal = Calendar.from_ical(raw.data)
                for component in cal.walk():
                    if component.name == "VEVENT":
                        evt = self._parse_vevent(component)
                        if evt:
                            events.append(evt)
            except Exception:
                continue

        return events

    async def create_event(self, event: CalendarEvent) -> str:
        """Create event on CalDAV server."""
        try:
            import caldav
            from icalendar import Calendar as ICalendar
            from icalendar import Event as IEvent
        except ImportError as _exc:
            raise ImportError("CalDAV support requires: pip install caldav icalendar") from _exc

        client = caldav.DAVClient(url=self.url, username=self.username, password=self.password)

        principal = client.principal()
        calendars = principal.calendars()

        if not calendars:
            raise RuntimeError("No calendars found")

        calendar = calendars[0]

        # Build iCalendar event
        ical = ICalendar()
        ical.add("prodid", "-//NAVIG//navig.run//")
        ical.add("version", "2.0")

        ievent = IEvent()
        ievent.add("summary", event.title)
        ievent.add("dtstart", event.start)
        ievent.add("dtend", event.end)
        ievent.add("uid", event.id or f"navig-{datetime.now().timestamp()}")

        if event.location:
            ievent.add("location", event.location)
        if event.description:
            ievent.add("description", event.description)

        ical.add_component(ievent)

        created = calendar.save_event(ical.to_ical().decode("utf-8"))
        return str(created.id) if created else ""

    def _parse_vevent(self, component) -> CalendarEvent | None:
        """Parse VEVENT (shared with ICSCalendarProvider)."""
        try:
            evt_start = component.get("dtstart")
            evt_end = component.get("dtend")

            if not evt_start:
                return None

            start_dt = evt_start.dt
            if not isinstance(start_dt, datetime):
                start_dt = datetime.combine(start_dt, datetime.min.time())

            if evt_end:
                end_dt = evt_end.dt
                if not isinstance(end_dt, datetime):
                    end_dt = datetime.combine(end_dt, datetime.max.time())
            else:
                end_dt = start_dt + timedelta(hours=1)

            return CalendarEvent(
                id=str(component.get("uid", "")),
                title=str(component.get("summary", "Untitled")),
                start=start_dt,
                end=end_dt,
                location=str(component.get("location", "")) or None,
                description=str(component.get("description", "")) or None,
            )
        except Exception:
            return None
