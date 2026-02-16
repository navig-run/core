"""
Proactive Assistance Module

Provides calendar, email, event-driven automation, and proactive
user engagement for NAVIG.

Engagement subsystem (inspired by OpenClaw patterns):
- UserStateTracker: Observes operator interaction patterns
- EngagementCoordinator: Decides when/what proactive actions to take
- CapabilityPromoter: Feature discovery engine
"""

from .providers import (
    CalendarEvent,
    EmailMessage,
    CalendarProvider,
    EmailProvider,
    MockCalendar,
    MockEmail,
)
from .engine import ProactiveEngine
from .user_state import UserStateTracker, OperatorState, TimeOfDay
from .engagement import EngagementCoordinator, EngagementConfig, EngagementAction, EngagementResult
from .capability_promo import CapabilityPromoter

# Optional: Google Calendar (needs google-api-python-client)
try:
    from .google_calendar import GoogleCalendar
except ImportError:
    GoogleCalendar = None

# Optional: ICS/CalDAV (needs icalendar, caldav)
try:
    from .ics_calendar import ICSCalendarProvider, CalDAVProvider
except ImportError:
    ICSCalendarProvider = None
    CalDAVProvider = None

# Optional: IMAP Email (needs imaplib - stdlib)
try:
    from .imap_email import IMAPEmailProvider, GmailProvider, OutlookProvider, FastmailProvider
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

