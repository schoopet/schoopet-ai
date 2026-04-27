"""Unit tests for TimeTool."""
from datetime import datetime
from unittest.mock import MagicMock

from agents.schoopet.time_tool import TimeTool


def test_get_current_time_with_explicit_timezone(tool_context):
    """Should return the current time in the requested timezone."""
    tool = TimeTool()

    result = tool.get_current_time("America/Los_Angeles", tool_context=tool_context)

    assert "Current time:" in result
    assert "America/Los_Angeles" in result
    assert "UTC" in result


def test_get_current_time_uses_saved_timezone(tool_context):
    """Should use the saved user timezone when none is provided."""
    tool = TimeTool()
    tool._preferences_tool = MagicMock()
    tool._preferences_tool.get_timezone_value.return_value = "America/New_York"

    result = tool.get_current_time(tool_context=tool_context)

    assert "America/New_York" in result
    tool._preferences_tool.get_timezone_value.assert_called_once_with(tool_context.user_id)


def test_get_current_time_falls_back_to_utc_without_preference():
    """Should fall back to UTC when no timezone information is available."""
    tool = TimeTool()
    tool._preferences_tool = MagicMock()
    tool._preferences_tool.get_timezone_value.return_value = None

    result = tool.get_current_time(tool_context=None)

    assert "UTC" in result


def test_get_current_time_rejects_invalid_timezone(tool_context):
    """Should reject invalid timezone strings."""
    tool = TimeTool()

    result = tool.get_current_time("Invalid/Timezone", tool_context=tool_context)

    assert "Invalid timezone" in result


def test_convert_time():
    """Should convert between timezones."""
    tool = TimeTool()

    result = tool.convert_time(
        "2026-04-27 17:00",
        "America/Los_Angeles",
        "America/New_York",
    )

    assert "Converted time:" in result
    assert "America/New_York" in result


def test_parse_natural_datetime_uses_saved_timezone(tool_context):
    """Should parse common natural datetime strings."""
    tool = TimeTool()
    tool._preferences_tool = MagicMock()
    tool._preferences_tool.get_timezone_value.return_value = "America/Los_Angeles"

    result = tool.parse_natural_datetime(
        "tomorrow at 9am",
        user_timezone="America/Los_Angeles",
        tool_context=tool_context,
    )

    assert "Parsed datetime:" in result


def test_next_occurrence_for_weekday_rule(tool_context):
    """Should compute the next recurrence for a weekday rule."""
    tool = TimeTool()
    tool._preferences_tool = MagicMock()
    tool._preferences_tool.get_timezone_value.return_value = "America/Los_Angeles"

    result = tool.next_occurrence(
        "every monday at 8am",
        user_timezone="America/Los_Angeles",
        tool_context=tool_context,
    )

    assert "Next occurrence:" in result
