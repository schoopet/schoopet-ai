"""Unit tests for CalendarTool."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.schoopet.calendar_tool import CalendarTool
from google.adk.tools import ToolContext

PHONE_NUMBER = "+14155551234"


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
    return CalendarTool()


def _set_service(tool: CalendarTool, events_resource: _FakeEventsResource):
    service = _FakeCalendarService(events_resource)
    tool._get_service = AsyncMock(return_value=service)
    return service


class TestCalendarTool:
    @pytest.mark.asyncio
    async def test_list_calendar_events_success(self, calendar_tool, tool_context):
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

        result = await calendar_tool.list_calendar_events(
            start_date="2024-01-01", tool_context=tool_context
        )

        assert "Found 1 event(s)" in result
        assert "Meeting with John" in result
        assert "[ID: event123abc]" in result
        assert events.list_calls[0]["calendarId"] == "primary"

    @pytest.mark.asyncio
    async def test_list_calendar_events_no_service(self, calendar_tool, tool_context):
        calendar_tool._get_service = AsyncMock(return_value=None)

        result = await calendar_tool.list_calendar_events(
            start_date="2024-01-01", tool_context=tool_context
        )

        assert result == ""

    @pytest.mark.asyncio
    async def test_create_calendar_event_success(self, calendar_tool, tool_context):
        events = _FakeEventsResource()
        _set_service(calendar_tool, events)

        result = await calendar_tool.create_calendar_event(
            title="New Event",
            start="2024-01-01T15:00:00",
            tool_context=tool_context,
        )

        assert "Created event: 'New Event'" in result
        body = events.insert_calls[0]["body"]
        assert body["summary"] == "New Event"
        assert body["start"]["timeZone"] == "UTC"

    @pytest.mark.asyncio
    async def test_get_calendar_status_connected(self, calendar_tool, tool_context):
        _set_service(calendar_tool, _FakeEventsResource())

        result = await calendar_tool.get_calendar_status(tool_context=tool_context)

        assert "connected" in result

    @pytest.mark.asyncio
    async def test_get_calendar_status_no_service(self, calendar_tool, tool_context):
        calendar_tool._get_service = AsyncMock(return_value=None)

        result = await calendar_tool.get_calendar_status(tool_context=tool_context)

        assert result == ""


class TestCalendarToolTimezone:
    @pytest.mark.asyncio
    async def test_list_events_invalid_timezone(self, calendar_tool, tool_context):
        result = await calendar_tool.list_calendar_events(
            start_date="2024-01-01",
            user_timezone="Invalid/Timezone",
            tool_context=tool_context,
        )

        assert "Invalid timezone" in result

    @pytest.mark.asyncio
    async def test_create_event_with_timezone(self, calendar_tool, tool_context):
        events = _FakeEventsResource()
        _set_service(calendar_tool, events)

        await calendar_tool.create_calendar_event(
            title="Meeting",
            start="2024-01-15T14:00:00",
            user_timezone="America/New_York",
            tool_context=tool_context,
        )

        body = events.insert_calls[0]["body"]
        assert body["start"]["timeZone"] == "America/New_York"
        assert body["end"]["timeZone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_update_event_with_timezone(self, calendar_tool, tool_context):
        events = _FakeEventsResource(
            existing_event={
                "id": "event123",
                "summary": "Old Title",
                "start": {"dateTime": "2024-01-15T10:00:00Z"},
                "end": {"dateTime": "2024-01-15T11:00:00Z"},
            }
        )
        _set_service(calendar_tool, events)

        result = await calendar_tool.update_calendar_event(
            event_id="event123",
            start="2024-01-15T15:00:00",
            user_timezone="Europe/London",
            tool_context=tool_context,
        )

        assert "Updated event" in result
        body = events.update_calls[0]["body"]
        assert body["start"]["timeZone"] == "Europe/London"


class TestListToUpdateFlow:
    @pytest.mark.asyncio
    async def test_list_returns_event_id_for_update(self, calendar_tool, tool_context):
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

        list_result = await calendar_tool.list_calendar_events(
            start_date="2024-01-15", tool_context=tool_context
        )
        assert f"[ID: {event_id}]" in list_result

        update_result = await calendar_tool.update_calendar_event(
            event_id=event_id,
            title="Daily Standup",
            location="Conference Room B",
            tool_context=tool_context,
        )

        assert "Updated event" in update_result
        body = events.update_calls[0]["body"]
        assert body["summary"] == "Daily Standup"
        assert body["location"] == "Conference Room B"

    @pytest.mark.asyncio
    async def test_list_multiple_events_with_ids(self, calendar_tool, tool_context):
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

        result = await calendar_tool.list_calendar_events(
            start_date="2024-01-15", tool_context=tool_context
        )

        assert "Found 3 event(s)" in result
        assert "[ID: event_001]" in result
        assert "[ID: event_002]" in result
        assert "[ID: event_003]" in result

    @pytest.mark.asyncio
    async def test_all_day_event_includes_id(self, calendar_tool, tool_context):
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

        result = await calendar_tool.list_calendar_events(
            start_date="2024-01-15", tool_context=tool_context
        )

        assert "[ID: allday_event_123]" in result
        assert "Company Holiday" in result
        assert "(all day)" in result


# ---------------------------------------------------------------------------
# Async test infrastructure
# ---------------------------------------------------------------------------

class _FakeEventsResourceAsync(_FakeEventsResource):
    """Extends _FakeEventsResource with delete support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delete_calls = []

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return _FakeRequest(None)


def _make_async_tool(events_resource: _FakeEventsResourceAsync):
    """Return a CalendarTool whose _get_service is an AsyncMock."""
    tool = CalendarTool()
    service = _FakeCalendarService(events_resource)
    tool._get_service = AsyncMock(return_value=service)
    return tool


@pytest.fixture
def async_tool_context():
    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = PHONE_NUMBER
    return ctx


# ---------------------------------------------------------------------------
# Attendees
# ---------------------------------------------------------------------------

class TestCreateAttendees:
    @pytest.mark.asyncio
    async def test_attendees_added_to_body(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Sync", start="2024-06-01T10:00:00",
            attendees=["alice@example.com", "bob@example.com"],
            tool_context=async_tool_context,
        )

        body = events.insert_calls[0]["body"]
        assert body["attendees"] == [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ]
        assert events.insert_calls[0]["sendUpdates"] == "all"

    @pytest.mark.asyncio
    async def test_no_attendees_no_send_updates(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Solo", start="2024-06-01T10:00:00",
            tool_context=async_tool_context,
        )

        assert "sendUpdates" not in events.insert_calls[0]
        assert "attendees" not in events.insert_calls[0]["body"]


# ---------------------------------------------------------------------------
# Google Meet
# ---------------------------------------------------------------------------

class TestCreateGoogleMeet:
    @pytest.mark.asyncio
    async def test_meet_sets_conference_data(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Video Call", start="2024-06-01T10:00:00",
            add_google_meet=True,
            tool_context=async_tool_context,
        )

        body = events.insert_calls[0]["body"]
        conf = body["conferenceData"]
        assert conf["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
        assert conf["createRequest"]["requestId"]  # non-empty
        assert events.insert_calls[0]["conferenceDataVersion"] == 1

    @pytest.mark.asyncio
    async def test_meet_unique_request_ids(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="A", start="2024-06-01T10:00:00", add_google_meet=True,
            tool_context=async_tool_context,
        )
        await tool.create_calendar_event(
            title="B", start="2024-06-02T10:00:00", add_google_meet=True,
            tool_context=async_tool_context,
        )

        id1 = events.insert_calls[0]["body"]["conferenceData"]["createRequest"]["requestId"]
        id2 = events.insert_calls[1]["body"]["conferenceData"]["createRequest"]["requestId"]
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_meet_link_in_response(self, async_tool_context):
        meet_url = "https://meet.google.com/abc-defg-hij"
        created = {
            "htmlLink": "https://calendar.google.com/event?id=x",
            "conferenceData": {
                "entryPoints": [{"entryPointType": "video", "uri": meet_url}]
            },
        }
        events = _FakeEventsResourceAsync(created_event=created)
        tool = _make_async_tool(events)

        result = await tool.create_calendar_event(
            title="Video", start="2024-06-01T10:00:00", add_google_meet=True,
            tool_context=async_tool_context,
        )

        assert f"Meet: {meet_url}" in result

    @pytest.mark.asyncio
    async def test_no_meet_no_conference_data(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Solo", start="2024-06-01T10:00:00",
            tool_context=async_tool_context,
        )

        assert "conferenceData" not in events.insert_calls[0]["body"]
        assert "conferenceDataVersion" not in events.insert_calls[0]


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

class TestCreateReminders:
    @pytest.mark.asyncio
    async def test_reminders_set_correctly(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Meeting", start="2024-06-01T10:00:00",
            reminders=[10, 30],
            tool_context=async_tool_context,
        )

        rem = events.insert_calls[0]["body"]["reminders"]
        assert rem["useDefault"] is False
        assert rem["overrides"] == [
            {"method": "popup", "minutes": 10},
            {"method": "popup", "minutes": 30},
        ]

    @pytest.mark.asyncio
    async def test_no_reminders_key_absent(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Meeting", start="2024-06-01T10:00:00",
            tool_context=async_tool_context,
        )

        assert "reminders" not in events.insert_calls[0]["body"]


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------

class TestCreateRecurrence:
    @pytest.mark.asyncio
    async def test_recurrence_wrapped_in_list(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Standup", start="2024-06-01T09:00:00",
            recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO",
            tool_context=async_tool_context,
        )

        assert events.insert_calls[0]["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]

    @pytest.mark.asyncio
    async def test_no_recurrence_key_absent(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="One-off", start="2024-06-01T09:00:00",
            tool_context=async_tool_context,
        )

        assert "recurrence" not in events.insert_calls[0]["body"]


# ---------------------------------------------------------------------------
# Color, visibility, transparency
# ---------------------------------------------------------------------------

class TestCreateOptionalFields:
    @pytest.mark.asyncio
    async def test_color_id_stored_as_string(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Red Event", start="2024-06-01T10:00:00",
            color_id=11,
            tool_context=async_tool_context,
        )

        assert events.insert_calls[0]["body"]["colorId"] == "11"

    @pytest.mark.asyncio
    async def test_visibility_private(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="Private", start="2024-06-01T10:00:00",
            visibility="private",
            tool_context=async_tool_context,
        )

        assert events.insert_calls[0]["body"]["visibility"] == "private"

    @pytest.mark.asyncio
    async def test_transparency_transparent(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        await tool.create_calendar_event(
            title="OOO", start="2024-06-01T10:00:00",
            transparency="transparent",
            tool_context=async_tool_context,
        )

        assert events.insert_calls[0]["body"]["transparency"] == "transparent"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDeleteCalendarEvent:
    @pytest.mark.asyncio
    async def test_delete_success(self, async_tool_context):
        events = _FakeEventsResourceAsync()
        tool = _make_async_tool(events)

        result = await tool.delete_calendar_event(
            event_id="abc123", tool_context=async_tool_context
        )

        assert events.delete_calls[0] == {"calendarId": "primary", "eventId": "abc123"}
        assert "Deleted event" in result

    @pytest.mark.asyncio
    async def test_delete_not_found(self, async_tool_context):
        from googleapiclient.errors import HttpError
        events = _FakeEventsResourceAsync()

        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"

        def _raise(**kwargs):
            raise HttpError(resp=resp, content=b"Not Found")

        events.delete = _raise
        tool = _make_async_tool(events)

        result = await tool.delete_calendar_event(
            event_id="missing", tool_context=async_tool_context
        )

        assert "Event not found" in result

    @pytest.mark.asyncio
    async def test_delete_no_user_id(self):
        ctx = MagicMock(spec=ToolContext)
        ctx.user_id = None
        tool = CalendarTool()

        result = await tool.delete_calendar_event(event_id="x", tool_context=ctx)

        assert result  # returns an error string


# ---------------------------------------------------------------------------
# Update with new fields
# ---------------------------------------------------------------------------

class TestUpdateNewFields:
    def _base_existing(self):
        return {
            "id": "ev1",
            "summary": "Existing",
            "start": {"dateTime": "2024-06-01T10:00:00Z"},
            "end": {"dateTime": "2024-06-01T11:00:00Z"},
        }

    @pytest.mark.asyncio
    async def test_update_attendees_sends_invites(self, async_tool_context):
        events = _FakeEventsResourceAsync(existing_event=self._base_existing())
        tool = _make_async_tool(events)

        await tool.update_calendar_event(
            event_id="ev1",
            attendees=["carol@example.com"],
            tool_context=async_tool_context,
        )

        body = events.update_calls[0]["body"]
        assert body["attendees"] == [{"email": "carol@example.com"}]
        assert events.update_calls[0]["sendUpdates"] == "all"

    @pytest.mark.asyncio
    async def test_update_no_attendees_no_send_updates(self, async_tool_context):
        events = _FakeEventsResourceAsync(existing_event=self._base_existing())
        tool = _make_async_tool(events)

        await tool.update_calendar_event(
            event_id="ev1", title="New Title",
            tool_context=async_tool_context,
        )

        assert "sendUpdates" not in events.update_calls[0]

    @pytest.mark.asyncio
    async def test_update_empty_attendees_removes_all(self, async_tool_context):
        events = _FakeEventsResourceAsync(existing_event=self._base_existing())
        tool = _make_async_tool(events)

        await tool.update_calendar_event(
            event_id="ev1", attendees=[],
            tool_context=async_tool_context,
        )

        assert events.update_calls[0]["body"]["attendees"] == []
        assert events.update_calls[0]["sendUpdates"] == "all"

    @pytest.mark.asyncio
    async def test_update_reminders(self, async_tool_context):
        events = _FakeEventsResourceAsync(existing_event=self._base_existing())
        tool = _make_async_tool(events)

        await tool.update_calendar_event(
            event_id="ev1", reminders=[5],
            tool_context=async_tool_context,
        )

        rem = events.update_calls[0]["body"]["reminders"]
        assert rem["overrides"] == [{"method": "popup", "minutes": 5}]

    @pytest.mark.asyncio
    async def test_update_recurrence(self, async_tool_context):
        events = _FakeEventsResourceAsync(existing_event=self._base_existing())
        tool = _make_async_tool(events)

        await tool.update_calendar_event(
            event_id="ev1", recurrence="RRULE:FREQ=DAILY",
            tool_context=async_tool_context,
        )

        assert events.update_calls[0]["body"]["recurrence"] == ["RRULE:FREQ=DAILY"]

    @pytest.mark.asyncio
    async def test_update_color_id(self, async_tool_context):
        events = _FakeEventsResourceAsync(existing_event=self._base_existing())
        tool = _make_async_tool(events)

        await tool.update_calendar_event(
            event_id="ev1", color_id=3,
            tool_context=async_tool_context,
        )

        assert events.update_calls[0]["body"]["colorId"] == "3"


# ---------------------------------------------------------------------------
# _format_event with new fields
# ---------------------------------------------------------------------------

class TestFormatEventNewFields:
    def _tool(self):
        return CalendarTool()

    def test_format_with_attendees(self):
        tool = self._tool()
        event = {
            "summary": "Sync",
            "start": {"dateTime": "2024-06-01T10:00:00Z"},
            "end": {"dateTime": "2024-06-01T11:00:00Z"},
            "attendees": [{"email": "a@x.com"}, {"email": "b@x.com"}],
        }
        result = tool._format_event(event)
        assert "attendees: a@x.com, b@x.com" in result

    def test_format_with_meet_link(self):
        tool = self._tool()
        event = {
            "summary": "Call",
            "start": {"dateTime": "2024-06-01T10:00:00Z"},
            "end": {"dateTime": "2024-06-01T11:00:00Z"},
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://meet.google.com/xyz"}
                ]
            },
        }
        result = tool._format_event(event)
        assert "meet: https://meet.google.com/xyz" in result

    def test_format_recurring_marker(self):
        tool = self._tool()
        event = {
            "summary": "Standup",
            "start": {"dateTime": "2024-06-01T09:00:00Z"},
            "end": {"dateTime": "2024-06-01T09:30:00Z"},
            "recurrence": ["RRULE:FREQ=WEEKLY"],
        }
        result = tool._format_event(event)
        assert "[recurring]" in result

    def test_format_without_new_fields_unchanged(self):
        tool = self._tool()
        event = {
            "id": "ev1",
            "summary": "Solo",
            "start": {"dateTime": "2024-06-01T10:00:00Z"},
            "end": {"dateTime": "2024-06-01T11:00:00Z"},
            "location": "Office",
        }
        result = tool._format_event(event)
        assert "Solo" in result
        assert "Office" in result
        assert "[ID: ev1]" in result
        assert "attendees" not in result
        assert "meet" not in result
        assert "recurring" not in result
