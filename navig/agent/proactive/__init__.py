"""
Proactive Assistance Module

Provides calendar, email, event-driven automation, and proactive
user engagement for NAVIG.

Engagement subsystem:
- UserStateTracker: Observes operator interaction patterns
- EngagementCoordinator: Decides when/what proactive actions to take
- CapabilityPromoter: Feature discovery engine
"""

from .capability_promo import CapabilityPromoter
from .engagement import (
    EngagementAction,
    EngagementConfig,
    EngagementCoordinator,
    EngagementResult,
)
from .engine import ProactiveEngine
from .providers import (
    CalendarEvent,
    CalendarProvider,
    EmailMessage,
    EmailProvider,
    MockCalendar,
    MockEmail,
)
from .user_state import OperatorState, TimeOfDay, UserStateTracker

# Optional: Google Calendar (needs google-api-python-client)
try:
    from .google_calendar import GoogleCalendar
except ImportError:
    GoogleCalendar = None

# Optional: ICS/CalDAV (needs icalendar, caldav)
try:
    from .ics_calendar import CalDAVProvider, ICSCalendarProvider
except ImportError:
    ICSCalendarProvider = None
    CalDAVProvider = None

# Optional: IMAP Email (needs imaplib - stdlib)
try:
    from .imap_email import (
        FastmailProvider,
        GmailProvider,
        IMAPEmailProvider,
        OutlookProvider,
    )
except ImportError:
    IMAPEmailProvider = None
    GmailProvider = None
    OutlookProvider = None
    FastmailProvider = None

__all__ = [
    # Core types
    "CalendarEvent",
    "EmailMessage",
    "CalendarProvider",
    "EmailProvider",
    # Mocks
    "MockCalendar",
    "MockEmail",
    # Engine
    "ProactiveEngine",
    # Calendar providers
    "GoogleCalendar",
    "ICSCalendarProvider",
    "CalDAVProvider",
    # Email providers
    "IMAPEmailProvider",
    "GmailProvider",
    "OutlookProvider",
    "FastmailProvider",
    # Engagement system
    "UserStateTracker",
    "OperatorState",
    "TimeOfDay",
    "EngagementCoordinator",
    "EngagementConfig",
    "EngagementAction",
    "EngagementResult",
    "CapabilityPromoter",
]
