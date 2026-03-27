"""
Proactive Assistance Providers

Interfaces for Calendar, Email, and other services that the
agent can proactively monitor and interact with.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    attendees: list[str] = None


@dataclass
class EmailMessage:
    id: str
    subject: str
    sender: str
    snippet: str
    received_at: datetime
    read: bool = False


class CalendarProvider(ABC):
    """Abstract interface for Calendar integration."""

    @abstractmethod
    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """List events in range."""
        pass

    @abstractmethod
    async def create_event(self, event: CalendarEvent) -> str:
        """Create new event, return ID."""
        pass


class EmailProvider(ABC):
    """Abstract interface for Email integration."""

    @abstractmethod
    async def list_unread(self, limit: int = 10) -> list[EmailMessage]:
        """Get unread messages."""
        pass

    @abstractmethod
    async def draft_email(self, to: list[str], subject: str, body: str) -> str:
        """Create draft email, return ID."""
        pass


class MockCalendar(CalendarProvider):
    """Mock implementation for testing."""

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        # Return a fake meeting
        return [
            CalendarEvent(
                id="mock-1",
                title="Weekly Sync",
                start=datetime.now().replace(hour=10, minute=0),
                end=datetime.now().replace(hour=11, minute=0),
                attendees=["alice@example.com", "bob@example.com"],
            )
        ]

    async def create_event(self, event: CalendarEvent) -> str:
        print(f"[MOCK] Created event: {event.title}")
        return "mock-event-id"


class MockEmail(EmailProvider):
    """Mock implementation for testing."""

    async def list_unread(self, limit: int = 10) -> list[EmailMessage]:
        return [
            EmailMessage(
                id="msg-1",
                subject="Project Update",
                sender="boss@company.com",
                snippet="Can we deploy today?",
                received_at=datetime.now(),
            )
        ]

    async def draft_email(self, to: list[str], subject: str, body: str) -> str:
        print(f"[MOCK] Drafted email to {to}: {subject}")
        return "mock-draft-id"
