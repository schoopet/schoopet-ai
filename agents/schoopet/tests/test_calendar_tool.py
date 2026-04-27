"""Unit tests for CalendarTool."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agents.schoopet.calendar_tool import CalendarTool
from google.adk.tools import ToolContext

PHONE_NUMBER = "+14155551234"
OAUTH_LINK = "https://example.com/oauth/google/initiate?token=abc&feature=calendar"


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeEventsResource:
    def __init__(self, items=None, existing_event=None, created_event=None):
        self.items = items or []
        self.existing_event = existing_event or {}
        self.created_event = created_event or {
            "htmlLink": "https://calendar.google.com/event?id=123"
        }
        self.list_calls = []
        self.insert_calls = []
        self.get_calls = []
        self.update_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeRequest({"items": self.items})

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return _FakeRequest(self.created_event)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _FakeRequest(self.existing_event)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return _FakeRequest({})


class _FakeCalendarService:
    def __init__(self, events_resource):
        self._events = events_resource

    def events(self):
        return self._events


@pytest.fixture
def tool_context():
    context = MagicMock(spec=ToolContext)
    context.user_id = PHONE_NUMBER
    return context


@pytest.fixture
def calendar_tool():
    with patch("agents.schoopet.calendar_tool.OAuthClient") as mock_client_cls:
        tool = CalendarTool()
        tool._oauth_client = mock_client_cls.return_value
        yield tool


def _set_service(calendar_tool: CalendarTool, events_resource: _FakeEventsResource):
    service = _FakeCalendarService(events_resource)
    calendar_tool._get_service = MagicMock(return_value=(service, None))
    return service


class TestCalendarTool:
    def test_list_calendar_events_success(self, calendar_tool, tool_context):
        events = _FakeEventsResource(
            items=[
                {
                    "id": "event123abc",
                    "summary": "Meeting with John",
                    "start": {"dateTime": "2024-01-01T10:00:00Z"},
                    "end": {"dateTime": "2024-01-01T11:00:00Z"},
                }
            ]
        )
        _set_service(calendar_tool, events)

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-01", tool_context=tool_context
        )

        assert "Found 1 event(s)" in result
        assert "Meeting with John" in result
        assert "[ID: event123abc]" in result
        assert events.list_calls[0]["calendarId"] == "primary"

    def test_list_calendar_events_no_token(self, calendar_tool, tool_context):
        calendar_tool._get_service = MagicMock(return_value=(None, OAUTH_LINK))

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-01", tool_context=tool_context
        )

        assert OAUTH_LINK in result

    def test_create_calendar_event_success(self, calendar_tool, tool_context):
        events = _FakeEventsResource()
        _set_service(calendar_tool, events)

        result = calendar_tool.create_calendar_event(
            title="New Event",
            start="2024-01-01T15:00:00",
            tool_context=tool_context,
        )

        assert "Created event: 'New Event'" in result
        body = events.insert_calls[0]["body"]
        assert body["summary"] == "New Event"
        assert body["start"]["timeZone"] == "UTC"

    def test_get_calendar_status_connected(self, calendar_tool, tool_context):
        calendar_tool._oauth_client.get_token_data.return_value = {
            "email": "user@example.com",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        calendar_tool._oauth_client.get_valid_access_token.return_value = "token"

        result = calendar_tool.get_calendar_status(tool_context=tool_context)

        assert result == "Calendar (user@example.com) is connected."

    def test_get_calendar_status_not_connected(self, calendar_tool, tool_context):
        calendar_tool._oauth_client.get_token_data.return_value = None
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK

        result = calendar_tool.get_calendar_status(tool_context=tool_context)

        assert result == f"Calendar is not connected. Authorize here:\n{OAUTH_LINK}"

    def test_get_calendar_status_expired_refresh_fail(self, calendar_tool, tool_context):
        calendar_tool._oauth_client.get_token_data.return_value = {
            "email": "user@example.com",
            "expires_at": datetime.now(timezone.utc),
        }
        calendar_tool._oauth_client.get_valid_access_token.return_value = None
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK

        result = calendar_tool.get_calendar_status(tool_context=tool_context)

        assert "connection has expired" in result
        assert OAUTH_LINK in result


class TestCalendarToolTimezone:
    def test_list_events_invalid_timezone(self, calendar_tool, tool_context):
        result = calendar_tool.list_calendar_events(
            start_date="2024-01-01",
            user_timezone="Invalid/Timezone",
            tool_context=tool_context,
        )

        assert "Invalid timezone" in result

    def test_create_event_with_timezone(self, calendar_tool, tool_context):
        events = _FakeEventsResource()
        _set_service(calendar_tool, events)

        calendar_tool.create_calendar_event(
            title="Meeting",
            start="2024-01-15T14:00:00",
            user_timezone="America/New_York",
            tool_context=tool_context,
        )

        body = events.insert_calls[0]["body"]
        assert body["start"]["timeZone"] == "America/New_York"
        assert body["end"]["timeZone"] == "America/New_York"

    def test_update_event_with_timezone(self, calendar_tool, tool_context):
        events = _FakeEventsResource(
            existing_event={
                "id": "event123",
                "summary": "Old Title",
                "start": {"dateTime": "2024-01-15T10:00:00Z"},
                "end": {"dateTime": "2024-01-15T11:00:00Z"},
            }
        )
        _set_service(calendar_tool, events)

        result = calendar_tool.update_calendar_event(
            event_id="event123",
            start="2024-01-15T15:00:00",
            user_timezone="Europe/London",
            tool_context=tool_context,
        )

        assert "Updated event" in result
        body = events.update_calls[0]["body"]
        assert body["start"]["timeZone"] == "Europe/London"


class TestListToUpdateFlow:
    def test_list_returns_event_id_for_update(self, calendar_tool, tool_context):
        event_id = "abc123xyz"
        events = _FakeEventsResource(
            items=[
                {
                    "id": event_id,
                    "summary": "Team Standup",
                    "start": {"dateTime": "2024-01-15T09:00:00Z"},
                    "end": {"dateTime": "2024-01-15T09:30:00Z"},
                    "location": "Conference Room A",
                }
            ],
            existing_event={
                "id": event_id,
                "summary": "Team Standup",
                "start": {"dateTime": "2024-01-15T09:00:00Z"},
                "end": {"dateTime": "2024-01-15T09:30:00Z"},
                "location": "Conference Room A",
            },
        )
        _set_service(calendar_tool, events)

        list_result = calendar_tool.list_calendar_events(
            start_date="2024-01-15", tool_context=tool_context
        )
        assert f"[ID: {event_id}]" in list_result

        update_result = calendar_tool.update_calendar_event(
            event_id=event_id,
            title="Daily Standup",
            location="Conference Room B",
            tool_context=tool_context,
        )

        assert "Updated event" in update_result
        body = events.update_calls[0]["body"]
        assert body["summary"] == "Daily Standup"
        assert body["location"] == "Conference Room B"

    def test_list_multiple_events_with_ids(self, calendar_tool, tool_context):
        events = _FakeEventsResource(
            items=[
                {
                    "id": "event_001",
                    "summary": "Morning Meeting",
                    "start": {"dateTime": "2024-01-15T09:00:00Z"},
                    "end": {"dateTime": "2024-01-15T10:00:00Z"},
                },
                {
                    "id": "event_002",
                    "summary": "Lunch with Client",
                    "start": {"dateTime": "2024-01-15T12:00:00Z"},
                    "end": {"dateTime": "2024-01-15T13:00:00Z"},
                },
                {
                    "id": "event_003",
                    "summary": "Project Review",
                    "start": {"dateTime": "2024-01-15T15:00:00Z"},
                    "end": {"dateTime": "2024-01-15T16:00:00Z"},
                },
            ]
        )
        _set_service(calendar_tool, events)

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-15", tool_context=tool_context
        )

        assert "Found 3 event(s)" in result
        assert "[ID: event_001]" in result
        assert "[ID: event_002]" in result
        assert "[ID: event_003]" in result

    def test_all_day_event_includes_id(self, calendar_tool, tool_context):
        events = _FakeEventsResource(
            items=[
                {
                    "id": "allday_event_123",
                    "summary": "Company Holiday",
                    "start": {"date": "2024-01-15"},
                    "end": {"date": "2024-01-15"},
                }
            ]
        )
        _set_service(calendar_tool, events)

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-15", tool_context=tool_context
        )

        assert "[ID: allday_event_123]" in result
        assert "Company Holiday" in result
        assert "(all day)" in result
