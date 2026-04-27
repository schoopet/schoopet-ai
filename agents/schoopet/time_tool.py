"""Time tool for getting the current date and time."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.adk.tools import ToolContext

from .preferences_tool import PreferencesTool


class TimeTool:
    """Tool for returning the current time in a requested or saved timezone."""

    def __init__(self):
        self._preferences_tool = PreferencesTool()

    def get_current_time(
        self,
        timezone_str: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Return the current time.

        If ``timezone_str`` is provided, use it. Otherwise, try the user's saved
        timezone preference. Fall back to UTC if neither is available.
        """
        resolved_timezone = timezone_str

        if not resolved_timezone and tool_context and getattr(tool_context, "user_id", None):
            resolved_timezone = self._preferences_tool.get_timezone_value(tool_context.user_id)

        if resolved_timezone:
            try:
                tz = ZoneInfo(resolved_timezone)
            except KeyError:
                return (
                    f"Invalid timezone: '{resolved_timezone}'. "
                    "Please use IANA timezone format like 'America/Los_Angeles'."
                )
        else:
            resolved_timezone = "UTC"
            tz = timezone.utc

        now = datetime.now(tz)
        offset = now.strftime("%z")
        formatted_offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"

        return (
            f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({resolved_timezone}, UTC{formatted_offset})"
        )

    def convert_time(
        self,
        source_time: str,
        source_tz: str,
        target_tz: str,
    ) -> str:
        """Convert a datetime from one timezone to another."""
        try:
            source_zone = ZoneInfo(source_tz)
            target_zone = ZoneInfo(target_tz)
        except KeyError as exc:
            return (
                f"Invalid timezone: '{exc.args[0]}'. "
                "Please use IANA timezone format like 'America/Los_Angeles'."
            )

        parsed = self._parse_absolute_datetime(source_time)
        if isinstance(parsed, str):
            return parsed

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=source_zone)
        else:
            parsed = parsed.astimezone(source_zone)

        converted = parsed.astimezone(target_zone)
        return (
            f"Converted time: {converted.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({target_tz}) from {parsed.strftime('%Y-%m-%d %H:%M:%S')} ({source_tz})"
        )

    def parse_natural_datetime(
        self,
        text: str,
        user_timezone: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Parse reminder-oriented natural datetime strings into exact ISO timestamps."""
        resolved_timezone, error = self._resolve_timezone(user_timezone, tool_context)
        if error:
            return error

        base_now = datetime.now(ZoneInfo(resolved_timezone))
        parsed = self._parse_natural(text, base_now)
        if isinstance(parsed, str):
            return parsed

        return (
            f"Parsed datetime: {parsed.isoformat()}\n"
            f"Timezone: {resolved_timezone}\n"
            f"UTC: {parsed.astimezone(timezone.utc).isoformat()}"
        )

    def next_occurrence(
        self,
        rule: str,
        user_timezone: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Calculate the next occurrence for common recurring reminder rules."""
        resolved_timezone, error = self._resolve_timezone(user_timezone, tool_context)
        if error:
            return error

        base_now = datetime.now(ZoneInfo(resolved_timezone))
        next_dt = self._parse_recurrence_rule(rule, base_now)
        if isinstance(next_dt, str):
            return next_dt

        return (
            f"Next occurrence: {next_dt.isoformat()}\n"
            f"Timezone: {resolved_timezone}\n"
            f"UTC: {next_dt.astimezone(timezone.utc).isoformat()}"
        )

    def _resolve_timezone(self, timezone_str: str, tool_context: ToolContext = None) -> tuple[str, str]:
        resolved_timezone = timezone_str
        if not resolved_timezone and tool_context and getattr(tool_context, "user_id", None):
            resolved_timezone = self._preferences_tool.get_timezone_value(tool_context.user_id)
        if not resolved_timezone:
            resolved_timezone = "UTC"
        try:
            ZoneInfo(resolved_timezone)
        except KeyError:
            return "", (
                f"Invalid timezone: '{resolved_timezone}'. "
                "Please use IANA timezone format like 'America/Los_Angeles'."
            )
        return resolved_timezone, ""

    def _parse_absolute_datetime(self, value: str):
        normalized = value.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return (
                "Could not parse datetime. Use ISO-like format such as "
                "'2026-04-27 17:30' or '2026-04-27T17:30:00'."
            )

    def _parse_natural(self, text: str, base_now: datetime):
        raw = text.strip()
        lowered = raw.lower().strip()

        absolute = self._parse_absolute_datetime(raw)
        if not isinstance(absolute, str):
            if absolute.tzinfo is None:
                return absolute.replace(
                    hour=base_now.hour,
                    minute=base_now.minute,
                    second=0,
                    microsecond=0,
                    tzinfo=base_now.tzinfo,
                ) if raw.strip() == absolute.strftime("%Y-%m-%d") else absolute.replace(tzinfo=base_now.tzinfo)
            return absolute.astimezone(base_now.tzinfo)

        relative_match = re.fullmatch(r"in (\d+) (minute|minutes|hour|hours|day|days|week|weeks)", lowered)
        if relative_match:
            count = int(relative_match.group(1))
            unit = relative_match.group(2)
            if "minute" in unit:
                return (base_now + timedelta(minutes=count)).replace(second=0, microsecond=0)
            if "hour" in unit:
                return (base_now + timedelta(hours=count)).replace(second=0, microsecond=0)
            if "day" in unit:
                return (base_now + timedelta(days=count)).replace(second=0, microsecond=0)
            return (base_now + timedelta(weeks=count)).replace(second=0, microsecond=0)

        today_tomorrow = re.fullmatch(r"(today|tomorrow)(?: at (.+))?", lowered)
        if today_tomorrow:
            day_word = today_tomorrow.group(1)
            time_part = today_tomorrow.group(2) or "09:00"
            target_date = base_now.date() if day_word == "today" else (base_now + timedelta(days=1)).date()
            return self._combine_date_and_time(target_date, time_part, base_now.tzinfo)

        next_weekday = re.fullmatch(r"next (monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?: at (.+))?", lowered)
        if next_weekday:
            target_weekday = self._weekday_index(next_weekday.group(1))
            time_part = next_weekday.group(2) or "09:00"
            days_ahead = (target_weekday - base_now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = (base_now + timedelta(days=days_ahead)).date()
            return self._combine_date_and_time(target_date, time_part, base_now.tzinfo)

        return (
            "Could not parse datetime. Supported forms include ISO datetimes, "
            "'today at 5pm', 'tomorrow at 9am', 'next monday at 8:30pm', and 'in 3 hours'."
        )

    def _parse_recurrence_rule(self, rule: str, base_now: datetime):
        lowered = rule.lower().strip()

        if lowered in {"daily", "every day"}:
            lowered = "daily at 09:00"

        daily = re.fullmatch(r"(?:daily|every day) at (.+)", lowered)
        if daily:
            return self._next_matching_day(base_now, None, daily.group(1))

        weekdays = re.fullmatch(r"(?:weekdays|every weekday) at (.+)", lowered)
        if weekdays:
            return self._next_matching_day(base_now, {0, 1, 2, 3, 4}, weekdays.group(1))

        weekends = re.fullmatch(r"(?:weekends|every weekend) at (.+)", lowered)
        if weekends:
            return self._next_matching_day(base_now, {5, 6}, weekends.group(1))

        weekly = re.fullmatch(
            r"(?:every |weekly on )(monday|tuesday|wednesday|thursday|friday|saturday|sunday) at (.+)",
            lowered,
        )
        if weekly:
            weekday = self._weekday_index(weekly.group(1))
            return self._next_matching_day(base_now, {weekday}, weekly.group(2))

        return (
            "Could not parse recurrence rule. Supported forms include "
            "'daily at 8am', 'weekdays at 9:30am', and 'every monday at 6pm'."
        )

    def _combine_date_and_time(self, target_date, time_part: str, tzinfo):
        parsed_time = self._parse_time_of_day(time_part)
        if isinstance(parsed_time, str):
            return parsed_time
        hour, minute = parsed_time
        return datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hour,
            minute,
            tzinfo=tzinfo,
        )

    def _parse_time_of_day(self, value: str):
        cleaned = value.strip().lower().replace(".", "")
        match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", cleaned)
        if not match:
            return "Could not parse time of day. Use forms like '9am', '5:30pm', or '17:30'."

        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3)

        if minute > 59:
            return "Invalid time of day."

        if meridiem:
            if hour < 1 or hour > 12:
                return "Invalid 12-hour clock time."
            if meridiem == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        elif hour > 23:
            return "Invalid 24-hour clock time."

        return hour, minute

    def _next_matching_day(self, base_now: datetime, allowed_weekdays: set[int] | None, time_part: str):
        parsed_time = self._parse_time_of_day(time_part)
        if isinstance(parsed_time, str):
            return parsed_time

        hour, minute = parsed_time
        for offset in range(0, 14):
            candidate_date = (base_now + timedelta(days=offset)).date()
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                tzinfo=base_now.tzinfo,
            )
            if allowed_weekdays is not None and candidate.weekday() not in allowed_weekdays:
                continue
            if candidate > base_now:
                return candidate
        return "Could not determine next occurrence."

    def _weekday_index(self, name: str) -> int:
        names = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        return names[name]
