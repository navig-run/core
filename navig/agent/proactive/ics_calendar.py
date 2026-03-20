"""
ICS/CalDAV Calendar Provider

Supports:
- Local .ics files
- Remote ICS URLs (Nextcloud, Fastmail, iCloud, etc.)
- CalDAV servers

No OAuth required - works with any standard ICS feed.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from navig.agent.proactive.providers import CalendarEvent, CalendarProvider


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
        url: Optional[str] = None,
        path: Optional[Path] = None,
        cache_minutes: int = 5
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
        self._cache: Optional[List[CalendarEvent]] = None
        self._cache_time: Optional[datetime] = None

    async def list_events(self, start: datetime, end: datetime) -> List[CalendarEvent]:
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

    async def _fetch_ics(self) -> Optional[str]:
        """Fetch ICS data from URL or file."""
        if self.url:
            try:
                import httpx
            except ImportError as _exc:
                raise ImportError("Remote ICS requires: pip install httpx") from _exc

            async with httpx.AsyncClient() as client:
                resp = await client.get(self.url, follow_redirects=True)
                if resp.status_code == 200:
                    return resp.text
                return None

        elif self.path and self.path.exists():
            return self.path.read_text(encoding='utf-8')

        return None

    def _parse_vevent(self, component) -> Optional[CalendarEvent]:
        """Parse an iCalendar VEVENT component."""
        try:
            evt_start = component.get('dtstart')
            evt_end = component.get('dtend')

            if not evt_start:
                return None

            start_dt = self._to_datetime(evt_start.dt)

            if evt_end:
                end_dt = self._to_datetime(evt_end.dt)
            else:
                # Default to 1 hour duration
                end_dt = start_dt + timedelta(hours=1)

            return CalendarEvent(
                id=str(component.get('uid', '')),
                title=str(component.get('summary', 'Untitled')),
                start=start_dt,
                end=end_dt,
                location=str(component.get('location', '')) or None,
                description=str(component.get('description', '')) or None,
                attendees=self._parse_attendees(component)
            )
        except Exception:
            return None

    def _to_datetime(self, dt) -> datetime:
        """Convert iCalendar date/datetime to Python datetime."""
        if isinstance(dt, datetime):
            return dt
        # It's a date, convert to datetime at midnight
        return datetime.combine(dt, datetime.min.time())

    def _parse_attendees(self, component) -> List[str]:
        """Extract attendee emails from VEVENT."""
        attendees = []
        for attendee in component.get('attendee', []):
            if hasattr(attendee, '__str__'):
                email = str(attendee).replace('mailto:', '')
                attendees.append(email)
        return attendees

    def _filter_events(
        self,
        events: List[CalendarEvent],
        start: datetime,
        end: datetime
    ) -> List[CalendarEvent]:
        """Filter events to those within the time range."""
        return [
            e for e in events
            if e.start >= start and e.start <= end
        ]


class CalDAVProvider(CalendarProvider):
    """
    CalDAV calendar provider for write-capable self-hosted calendars.
    
    Supports Nextcloud, Radicale, Baikal, etc.
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str
    ):
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

    async def list_events(self, start: datetime, end: datetime) -> List[CalendarEvent]:
        """List events from CalDAV server."""
        try:
            import caldav
        except ImportError as _exc:
            raise ImportError("CalDAV support requires: pip install caldav") from _exc

        client = caldav.DAVClient(
            url=self.url,
            username=self.username,
            password=self.password
        )

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

        client = caldav.DAVClient(
            url=self.url,
            username=self.username,
            password=self.password
        )

        principal = client.principal()
        calendars = principal.calendars()

        if not calendars:
            raise RuntimeError("No calendars found")

        calendar = calendars[0]

        # Build iCalendar event
        ical = ICalendar()
        ical.add('prodid', '-//NAVIG//navig.run//')
        ical.add('version', '2.0')

        ievent = IEvent()
        ievent.add('summary', event.title)
        ievent.add('dtstart', event.start)
        ievent.add('dtend', event.end)
        ievent.add('uid', event.id or f"navig-{datetime.now().timestamp()}")

        if event.location:
            ievent.add('location', event.location)
        if event.description:
            ievent.add('description', event.description)

        ical.add_component(ievent)

        created = calendar.save_event(ical.to_ical().decode('utf-8'))
        return str(created.id) if created else ""

    def _parse_vevent(self, component) -> Optional[CalendarEvent]:
        """Parse VEVENT (shared with ICSCalendarProvider)."""
        try:
            evt_start = component.get('dtstart')
            evt_end = component.get('dtend')

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
                id=str(component.get('uid', '')),
                title=str(component.get('summary', 'Untitled')),
                start=start_dt,
                end=end_dt,
                location=str(component.get('location', '')) or None,
                description=str(component.get('description', '')) or None
            )
        except Exception:
            return None
