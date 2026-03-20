"""
Google Calendar Implementation.

Provides real integration with Google Calendar API.
"""
import datetime
import os.path
import pickle
from typing import List

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .providers import CalendarEvent, CalendarProvider

# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendar(CalendarProvider):
    """Google Calendar implementation."""

    def __init__(self, credentials_path: str = "credentials.json", token_path: str = "token.pickle"):
        self.creds = None
        self.service = None
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Google API."""
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                self.creds = pickle.load(token)

        # If there are no (valid) credentials available, let the user log in.
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    print("Credentials file not found. Running in mock mode.")
                    return

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES)
                self.creds = flow.run_local_server(port=0)

            # Save the credentials for the next run
            with open(self.token_path, 'wb') as token:
                pickle.dump(self.creds, token)

        if self.creds:
            self.service = build('calendar', 'v3', credentials=self.creds)

    async def list_events(self, start: datetime.datetime, end: datetime.datetime) -> List[CalendarEvent]:
        """List events from primary calendar."""
        if not self.service:
            print("[GoogleCalendar] Not authenticated, returning empty list.")
            return []

        events_result = self.service.events().list(
            calendarId='primary',
            timeMin=start.isoformat() + 'Z',
            timeMax=end.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        result = []

        for event in events:
            # Parse start/end (handling all-day events)
            start_dt = event['start'].get('dateTime', event['start'].get('date'))
            end_dt = event['end'].get('dateTime', event['end'].get('date'))

            # Simple ISO parsing (naive implementation for demo)
            # In production, use dateutil.parser

            result.append(CalendarEvent(
                id=event['id'],
                title=event.get('summary', 'No Title'),
                start=datetime.datetime.fromisoformat(start_dt.replace('Z', '+00:00')) if 'T' in start_dt else datetime.datetime.strptime(start_dt, "%Y-%m-%d"),
                end=datetime.datetime.fromisoformat(end_dt.replace('Z', '+00:00')) if 'T' in end_dt else datetime.datetime.strptime(end_dt, "%Y-%m-%d"),
                location=event.get('location'),
                description=event.get('description')
            ))

        return result

    async def create_event(self, event: CalendarEvent) -> str:
        """Create a new event."""
        if not self.service:
            return "auth-failed"

        event_body = {
            'summary': event.title,
            'location': event.location,
            'description': event.description,
            'start': {
                'dateTime': event.start.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': event.end.isoformat(),
                'timeZone': 'UTC',
            },
            'attendees': [{'email': email} for email in (event.attendees or [])],
        }

        created_event = self.service.events().insert(calendarId='primary', body=event_body).execute()
        return created_event.get('id')
