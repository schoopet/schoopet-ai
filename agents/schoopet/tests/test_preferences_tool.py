"""Unit tests for PreferencesTool."""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from agents.schoopet.preferences_tool import PreferencesTool
from google.adk.tools import ToolContext

# Sample data
PHONE_NUMBER = "+14155551234"
NORMALIZED_PHONE = "14155551234"
TIMEZONE_LA = "America/Los_Angeles"
TIMEZONE_NY = "America/New_York"


@pytest.fixture
def tool_context():
    """Create a mock ToolContext."""
    context = MagicMock(spec=ToolContext)
    context.user_id = PHONE_NUMBER
    return context


@pytest.fixture
def preferences_tool():
    """Create a PreferencesTool instance with mocked Firestore."""
    with patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
        with patch("google.cloud.firestore.Client") as mock_client_cls:
            tool = PreferencesTool()
            # Configure the mock
            mock_client = mock_client_cls.return_value
            tool._firestore_client = mock_client
            yield tool


class TestPreferencesTool:
    """Tests for PreferencesTool class."""

    def test_normalize_user_id(self):
        """Should normalize phone numbers consistently."""
        from agents.schoopet.utils import normalize_user_id
        assert normalize_user_id("+14155551234") == "14155551234"
        assert normalize_user_id("1-415-555-1234") == "14155551234"
        assert normalize_user_id("14155551234") == "14155551234"
        assert normalize_user_id("+1 415 555 1234") == "14155551234"

    def test_set_timezone_no_context(self, preferences_tool):
        """Should return error when no tool_context provided."""
        result = preferences_tool.set_timezone(TIMEZONE_LA)
        assert "ERROR" in result
        assert "no user_id" in result

    def test_set_timezone_invalid_timezone(self, preferences_tool, tool_context):
        """Should reject invalid timezone strings."""
        result = preferences_tool.set_timezone("Invalid/Timezone", tool_context=tool_context)
        assert "Invalid timezone" in result
        assert "IANA" in result

    def test_set_timezone_success(self, preferences_tool, tool_context):
        """Should save timezone successfully."""
        # Setup mock
        mock_doc_ref = MagicMock()
        preferences_tool._firestore_client.collection.return_value.document.return_value = mock_doc_ref

        result = preferences_tool.set_timezone(TIMEZONE_LA, tool_context=tool_context)

        assert "Timezone set to America/Los_Angeles" in result
        preferences_tool._firestore_client.collection.assert_called_with("user_preferences")
        preferences_tool._firestore_client.collection().document.assert_called_with(NORMALIZED_PHONE)
        mock_doc_ref.set.assert_called_once()

        # Verify the saved data contains the timezone
        call_args = mock_doc_ref.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["timezone"] == TIMEZONE_LA
        assert "updated_at" in saved_data
        assert saved_data["phone_number"] == PHONE_NUMBER

    def test_get_timezone_no_context(self, preferences_tool):
        """Should return error when no tool_context provided."""
        result = preferences_tool.get_timezone()
        assert "ERROR" in result
        assert "no user_id" in result

    def test_get_timezone_not_set(self, preferences_tool, tool_context):
        """Should return helpful message when timezone not set."""
        mock_doc = MagicMock()
        mock_doc.exists = False
        preferences_tool._firestore_client.collection.return_value.document.return_value.get.return_value = mock_doc

        result = preferences_tool.get_timezone(tool_context=tool_context)

        assert "No timezone set" in result
        assert "set_timezone" in result

    def test_get_timezone_success(self, preferences_tool, tool_context):
        """Should return timezone when set."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"timezone": TIMEZONE_NY}
        preferences_tool._firestore_client.collection.return_value.document.return_value.get.return_value = mock_doc

        result = preferences_tool.get_timezone(tool_context=tool_context)

        assert "User timezone: America/New_York" in result

    def test_get_timezone_value_not_set(self, preferences_tool):
        """Should return None when timezone not set (internal method)."""
        mock_doc = MagicMock()
        mock_doc.exists = False
        preferences_tool._firestore_client.collection.return_value.document.return_value.get.return_value = mock_doc

        result = preferences_tool.get_timezone_value(PHONE_NUMBER)

        assert result is None

    def test_get_timezone_value_success(self, preferences_tool):
        """Should return timezone string when set (internal method)."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"timezone": TIMEZONE_LA}
        preferences_tool._firestore_client.collection.return_value.document.return_value.get.return_value = mock_doc

        result = preferences_tool.get_timezone_value(PHONE_NUMBER)

        assert result == TIMEZONE_LA


class TestTimezoneValidation:
    """Tests for timezone validation."""

    @pytest.fixture
    def preferences_tool_with_mock(self):
        """Create tool with mocked Firestore that accepts saves."""
        with patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            with patch("google.cloud.firestore.Client") as mock_client_cls:
                tool = PreferencesTool()
                mock_client = mock_client_cls.return_value
                mock_client.collection.return_value.document.return_value.set.return_value = None
                tool._firestore_client = mock_client
                yield tool

    @pytest.fixture
    def tool_context(self):
        """Create a mock ToolContext."""
        context = MagicMock(spec=ToolContext)
        context.user_id = PHONE_NUMBER
        return context

    def test_valid_us_timezones(self, preferences_tool_with_mock, tool_context):
        """Should accept valid US timezone strings."""
        valid_timezones = [
            "America/Los_Angeles",
            "America/Denver",
            "America/Chicago",
            "America/New_York",
            "America/Phoenix",
            "Pacific/Honolulu",
        ]

        for tz in valid_timezones:
            result = preferences_tool_with_mock.set_timezone(tz, tool_context=tool_context)
            assert "Timezone set to" in result, f"Failed for {tz}"

    def test_valid_international_timezones(self, preferences_tool_with_mock, tool_context):
        """Should accept valid international timezone strings."""
        valid_timezones = [
            "Europe/London",
            "Europe/Paris",
            "Asia/Tokyo",
            "Asia/Shanghai",
            "Australia/Sydney",
        ]

        for tz in valid_timezones:
            result = preferences_tool_with_mock.set_timezone(tz, tool_context=tool_context)
            assert "Timezone set to" in result, f"Failed for {tz}"

    def test_invalid_timezone_formats(self, preferences_tool_with_mock, tool_context):
        """Should reject invalid timezone formats."""
        truly_invalid = ["Invalid/Zone", "America", "GMT+8"]

        for tz in truly_invalid:
            result = preferences_tool_with_mock.set_timezone(tz, tool_context=tool_context)
            assert "Invalid timezone" in result, f"Should have rejected {tz}"
