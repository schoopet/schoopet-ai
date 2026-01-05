"""Unit tests for CalendarTool."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, ANY
from agents.shoopet.calendar_tool import CalendarTool
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
    with patch("agents.shoopet.calendar_tool.OAuthClient") as mock_client_cls:
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
                    "summary": "Meeting with John",
                    "start": {"dateTime": "2024-01-01T10:00:00Z"},
                    "end": {"dateTime": "2024-01-01T11:00:00Z"}
                }
            ]
        }
        
        result = calendar_tool.list_calendar_events(start_date="2024-01-01", tool_context=tool_context)
        
        assert "Found 1 event(s)" in result
        assert "Meeting with John" in result
        
        calendar_tool._oauth_client.get_valid_access_token.assert_called_with(PHONE_NUMBER, "calendar")
        mock_client.get.assert_called()

    @patch("httpx.Client")
    def test_list_calendar_events_no_token(self, mock_httpx, calendar_tool, tool_context):
        """Should return auth link if no token."""
        # Mock no token
        calendar_tool._oauth_client.get_valid_access_token.return_value = None
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        result = calendar_tool.list_calendar_events(start_date="2024-01-01", tool_context=tool_context)
        
        assert "Your Google Calendar is not connected yet" in result
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
        calendar_tool._oauth_client.is_token_expired.return_value = False
        
        result = calendar_tool.get_calendar_status(tool_context=tool_context)
        assert "Google Calendar is connected to: user@example.com" in result

    def test_get_calendar_status_not_connected(self, calendar_tool, tool_context):
        """Should return auth link if not connected."""
        calendar_tool._oauth_client.get_token_data.return_value = None
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        result = calendar_tool.get_calendar_status(tool_context=tool_context)
        assert "Google Calendar is not connected" in result
        assert OAUTH_LINK in result

    def test_get_calendar_status_expired_refresh_fail(self, calendar_tool, tool_context):
        """Should return auth link if refresh fails."""
        mock_token_data = {"email": "user@example.com", "expires_at": datetime.now(timezone.utc)}
        
        calendar_tool._oauth_client.get_token_data.return_value = mock_token_data
        calendar_tool._oauth_client.is_token_expired.return_value = True
        calendar_tool._oauth_client.get_valid_access_token.return_value = None # Refresh failed
        calendar_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        result = calendar_tool.get_calendar_status(tool_context=tool_context)
        assert "connection has expired" in result
        assert OAUTH_LINK in result
