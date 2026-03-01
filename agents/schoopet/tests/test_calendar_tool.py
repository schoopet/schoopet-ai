"""Unit tests for CalendarTool."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, ANY
from agents.schoopet.calendar_tool import CalendarTool
from google.adk.tools import ToolContext

# Sample data
PHONE_NUMBER = "+14155551234"
ACCESS_TOKEN = "access-token-123"
OAUTH_LINK = "https://example.com/oauth/google/initiate?token=abc&feature=calendar"

@pytest.fixture
def tool_context():
    """Create a mock ToolContext."""
    context = MagicMock(spec=ToolContext)
    context.user_id = PHONE_NUMBER
    return context

@pytest.fixture
def calendar_tool():
    """Create a CalendarTool instance with mocked OAuthClient."""
    with patch("agents.schoopet.calendar_tool.OAuthClient") as mock_client_cls:
        tool = CalendarTool()
        
        # Configure the mock client instance attached to the tool
        mock_client = mock_client_cls.return_value
        tool._oauth_client = mock_client
        
        yield tool

class TestCalendarTool:
    """Tests for CalendarTool class."""

    def test_initialization(self, calendar_tool):
        """Should initialize OAuthClient."""
        assert calendar_tool._oauth_client is not None

    @patch("httpx.Client")
    def test_list_calendar_events_success(self, mock_httpx, calendar_tool, tool_context):
        """Should list calendar events successfully when authorized."""
        # Mock valid token from OAuthClient
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "items": [
                {
                    "id": "event123abc",
                    "summary": "Meeting with John",
                    "start": {"dateTime": "2024-01-01T10:00:00Z"},
                    "end": {"dateTime": "2024-01-01T11:00:00Z"}
                }
            ]
        }

        result = calendar_tool.list_calendar_events(start_date="2024-01-01", tool_context=tool_context)

        assert "Found 1 event(s)" in result
        assert "Meeting with John" in result
        assert "[ID: event123abc]" in result

        calendar_tool._oauth_client.get_valid_access_token.assert_called_with(PHONE_NUMBER, "calendar")
        mock_client.get.assert_called()

    @patch("httpx.Client")
    def test_list_calendar_events_no_token(self, mock_httpx, calendar_tool, tool_context):
        """Should return auth link if no token."""
        # Mock no token
        calendar_tool._oauth_client.get_valid_access_token.return_value = None
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        result = calendar_tool.list_calendar_events(start_date="2024-01-01", tool_context=tool_context)
        
        assert "can't access your calendar" in result
        assert OAUTH_LINK in result
        
        calendar_tool._oauth_client.get_oauth_link.assert_called_with(PHONE_NUMBER, "calendar")

    @patch("httpx.Client")
    def test_create_calendar_event_success(self, mock_httpx, calendar_tool, tool_context):
        """Should create a calendar event successfully."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN
        
        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "htmlLink": "https://calendar.google.com/event?id=123"
        }
        
        result = calendar_tool.create_calendar_event(
            title="New Event",
            start="2024-01-01T15:00:00",
            tool_context=tool_context
        )
        
        assert "Created event: 'New Event'" in result
        assert "https://calendar.google.com/event?id=123" in result
        mock_client.post.assert_called()

    def test_get_calendar_status_connected(self, calendar_tool, tool_context):
        """Should return connected status with email."""
        mock_token_data = {
            "email": "user@example.com",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        
        calendar_tool._oauth_client.get_token_data.return_value = mock_token_data
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        result = calendar_tool.get_calendar_status(tool_context=tool_context)
        assert "Personal account (user@example.com) is authorized." in result

    def test_get_calendar_status_not_connected(self, calendar_tool, tool_context):
        """Should return auth link if not connected."""
        calendar_tool._oauth_client.get_token_data.return_value = None
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        result = calendar_tool.get_calendar_status(tool_context=tool_context)
        assert "Personal account is not connected" in result
        assert OAUTH_LINK in result

    def test_get_calendar_status_expired_refresh_fail(self, calendar_tool, tool_context):
        """Should return auth link if refresh fails."""
        mock_token_data = {"email": "user@example.com", "expires_at": datetime.now(timezone.utc)}

        calendar_tool._oauth_client.get_token_data.return_value = mock_token_data
        calendar_tool._oauth_client.get_valid_access_token.return_value = None # Refresh failed
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK

        result = calendar_tool.get_calendar_status(tool_context=tool_context)
        assert "connection has expired" in result
        assert OAUTH_LINK in result


class TestCalendarToolTimezone:
    """Tests for timezone handling in CalendarTool."""

    @patch("httpx.Client")
    def test_list_events_with_timezone(self, mock_httpx, calendar_tool, tool_context):
        """Should use provided timezone for date calculations."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {"items": []}

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-01",
            user_timezone="America/Los_Angeles",
            tool_context=tool_context
        )

        assert "No events found" in result
        # Verify API was called (timezone affects the UTC conversion)
        mock_client.get.assert_called()

    @patch("httpx.Client")
    def test_list_events_invalid_timezone(self, mock_httpx, calendar_tool, tool_context):
        """Should reject invalid timezone."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-01",
            user_timezone="Invalid/Timezone",
            tool_context=tool_context
        )

        assert "Invalid timezone" in result

    @patch("httpx.Client")
    def test_create_event_with_timezone(self, mock_httpx, calendar_tool, tool_context):
        """Should create event with provided timezone."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "htmlLink": "https://calendar.google.com/event?id=123"
        }

        result = calendar_tool.create_calendar_event(
            title="Meeting",
            start="2024-01-15T14:00:00",
            user_timezone="America/New_York",
            tool_context=tool_context
        )

        assert "Created event" in result

        # Verify the event was created with the correct timezone
        call_args = mock_client.post.call_args
        event_data = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert event_data["start"]["timeZone"] == "America/New_York"
        assert event_data["end"]["timeZone"] == "America/New_York"

    @patch("httpx.Client")
    def test_create_event_default_timezone(self, mock_httpx, calendar_tool, tool_context):
        """Should use UTC as default timezone."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "htmlLink": "https://calendar.google.com/event?id=123"
        }

        result = calendar_tool.create_calendar_event(
            title="Meeting",
            start="2024-01-15T14:00:00",
            tool_context=tool_context
        )

        assert "Created event" in result

        # Verify default UTC timezone
        call_args = mock_client.post.call_args
        event_data = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert event_data["start"]["timeZone"] == "UTC"

    @patch("httpx.Client")
    def test_update_event_with_timezone(self, mock_httpx, calendar_tool, tool_context):
        """Should update event with provided timezone."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value

        # Mock GET for existing event
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "id": "event123",
            "summary": "Old Title",
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T11:00:00Z"}
        }

        # Mock PUT for update
        mock_client.put.return_value.status_code = 200
        mock_client.put.return_value.json.return_value = {}

        result = calendar_tool.update_calendar_event(
            event_id="event123",
            start="2024-01-15T15:00:00",
            user_timezone="Europe/London",
            tool_context=tool_context
        )

        assert "Updated event" in result

        # Verify timezone in update
        call_args = mock_client.put.call_args
        event_data = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert event_data["start"]["timeZone"] == "Europe/London"


class TestListToUpdateFlow:
    """Tests for the list_calendar_events -> update_calendar_event flow."""

    @patch("httpx.Client")
    def test_list_returns_event_id_for_update(self, mock_httpx, calendar_tool, tool_context):
        """Should be able to use event ID from list to update an event."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value

        # Step 1: List events - returns events with IDs
        event_id = "abc123xyz"
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "items": [
                {
                    "id": event_id,
                    "summary": "Team Standup",
                    "start": {"dateTime": "2024-01-15T09:00:00Z"},
                    "end": {"dateTime": "2024-01-15T09:30:00Z"},
                    "location": "Conference Room A"
                }
            ]
        }

        list_result = calendar_tool.list_calendar_events(
            start_date="2024-01-15",
            tool_context=tool_context
        )

        # Verify event ID is in the output
        assert f"[ID: {event_id}]" in list_result
        assert "Team Standup" in list_result

        # Step 2: Update the event using the ID from list
        # Reset mock for update flow
        mock_client.get.return_value.json.return_value = {
            "id": event_id,
            "summary": "Team Standup",
            "start": {"dateTime": "2024-01-15T09:00:00Z"},
            "end": {"dateTime": "2024-01-15T09:30:00Z"},
            "location": "Conference Room A"
        }
        mock_client.put.return_value.status_code = 200
        mock_client.put.return_value.json.return_value = {}

        update_result = calendar_tool.update_calendar_event(
            event_id=event_id,
            title="Daily Standup",
            location="Conference Room B",
            tool_context=tool_context
        )

        assert "Updated event" in update_result

        # Verify the update was called with the correct event ID
        put_call = mock_client.put.call_args
        assert event_id in put_call[0][0]  # ID should be in the URL

        # Verify the update payload has new values
        event_data = put_call.kwargs.get("json", put_call[1].get("json", {}))
        assert event_data["summary"] == "Daily Standup"
        assert event_data["location"] == "Conference Room B"

    @patch("httpx.Client")
    def test_list_multiple_events_with_ids(self, mock_httpx, calendar_tool, tool_context):
        """Should return IDs for all listed events."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "items": [
                {
                    "id": "event_001",
                    "summary": "Morning Meeting",
                    "start": {"dateTime": "2024-01-15T09:00:00Z"},
                    "end": {"dateTime": "2024-01-15T10:00:00Z"}
                },
                {
                    "id": "event_002",
                    "summary": "Lunch with Client",
                    "start": {"dateTime": "2024-01-15T12:00:00Z"},
                    "end": {"dateTime": "2024-01-15T13:00:00Z"}
                },
                {
                    "id": "event_003",
                    "summary": "Project Review",
                    "start": {"dateTime": "2024-01-15T15:00:00Z"},
                    "end": {"dateTime": "2024-01-15T16:00:00Z"}
                }
            ]
        }

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-15",
            tool_context=tool_context
        )

        assert "Found 3 event(s)" in result
        assert "[ID: event_001]" in result
        assert "[ID: event_002]" in result
        assert "[ID: event_003]" in result

    @patch("httpx.Client")
    def test_all_day_event_includes_id(self, mock_httpx, calendar_tool, tool_context):
        """Should include ID for all-day events."""
        calendar_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN

        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "items": [
                {
                    "id": "allday_event_123",
                    "summary": "Company Holiday",
                    "start": {"date": "2024-01-15"},
                    "end": {"date": "2024-01-15"}
                }
            ]
        }

        result = calendar_tool.list_calendar_events(
            start_date="2024-01-15",
            tool_context=tool_context
        )

        assert "[ID: allday_event_123]" in result
        assert "Company Holiday" in result
        assert "(all day)" in result
