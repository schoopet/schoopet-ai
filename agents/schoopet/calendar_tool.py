"""Google Calendar tool for agent using OAuth tokens.

Token resolution order (per operation):
1. System token (workspace_system) — succeeds if the calendar is shared with the agent account.
2. User personal token (calendar) — backwards-compatible fallback.
3. Neither works → two-option message (authorize as you, or share with agent account).
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo
from google.adk.tools import ToolContext
from .oauth_client import OAuthClient

# Google Calendar API endpoints
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# System-level shared agent account constants
CALENDAR_SYSTEM_USER_ID = "email_system"
CALENDAR_SYSTEM_FEATURE = "workspace_system"
AGENT_EMAIL = "schoopet.agent@gmail.com"

SLACK_ALLOWED_TEAM_ID = os.getenv("SLACK_ALLOWED_TEAM_ID", "")


def _is_allowed_slack_user(user_id: str, oauth_client: "OAuthClient") -> bool:
    """Return True only if user belongs to the configured Slack workspace."""
    if not SLACK_ALLOWED_TEAM_ID:
        return False
    return oauth_client.get_slack_team_id(user_id) == SLACK_ALLOWED_TEAM_ID


def _no_calendar_access(phone: str, oauth_client: "OAuthClient") -> str:
    link = oauth_client.get_oauth_link(phone, "calendar")
    return (
        f"Schoopet's shared account ({AGENT_EMAIL}) can't access your calendar.\n\n"
        f"**Option 1 – Authorize me to act as you**:\n{link}\n\n"
        f"**Option 2 – Share with Schoopet**: Share your Google Calendar with "
        f"{AGENT_EMAIL} (add as reader/editor in Calendar settings)."
    )


class CalendarTool:
    """Tool for accessing Google Calendar via OAuth tokens.

    Tries the shared agent account first, then falls back to the user's
    personal "calendar" OAuth token. If neither works, returns a two-option
    message explaining how to grant access.
    """

    def __init__(self):
        """Initialize the Calendar Tool."""
        self._oauth_client = OAuthClient()

    def _format_event(self, event: Dict[str, Any]) -> str:
        """Format a calendar event for display."""
        event_id = event.get("id", "")
        summary = event.get("summary", "No title")
        start = event.get("start", {})
        end = event.get("end", {})

        # Handle all-day events vs timed events
        if "date" in start:
            start_str = start["date"]
            end_str = end.get("date", "")
            time_str = f"{start_str}" + (f" to {end_str}" if end_str != start_str else " (all day)")
        else:
            start_dt = start.get("dateTime", "")
            end_dt = end.get("dateTime", "")
            try:
                start_parsed = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                end_parsed = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                time_str = f"{start_parsed.strftime('%b %d, %I:%M %p')} - {end_parsed.strftime('%I:%M %p')}"
            except:
                time_str = f"{start_dt} - {end_dt}"

        location = event.get("location", "")

        result = f"- {summary}: {time_str}"
        if location:
            result += f" @ {location}"
        if event_id:
            result += f" [ID: {event_id}]"

        return result

    # ── Private token-specific helpers ─────────────────────────────────────────

    def _list_events_with_token(
        self,
        token: str,
        start_dt: datetime,
        end_dt: datetime,
        max_results: int,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> Optional[str]:
        """Attempt to list events using the given token. Returns None on 401/403."""
        import httpx

        with httpx.Client() as client:
            response = client.get(
                f"{CALENDAR_API_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "timeMin": start_dt.isoformat(),
                    "timeMax": end_dt.isoformat(),
                    "maxResults": max_results,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )

        if response.status_code in (401, 403):
            return None

        response.raise_for_status()
        events = response.json().get("items", [])
        if not events:
            return f"No events found between {start_date or 'today'} and {end_date or '7 days from now'}."

        formatted_events = [self._format_event(event) for event in events]
        return f"Found {len(events)} event(s):\n" + "\n".join(formatted_events)

    def _create_event_with_token(
        self,
        token: str,
        event: dict,
        title: str,
        start: str,
    ) -> Optional[str]:
        """Attempt to create an event using the given token. Returns None on 401/403."""
        import httpx

        with httpx.Client() as client:
            response = client.post(
                f"{CALENDAR_API_BASE}/calendars/primary/events",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )

        if response.status_code in (401, 403):
            return None

        response.raise_for_status()
        created_event = response.json()
        event_link = created_event.get("htmlLink", "")
        return f"Created event: '{title}' on {start}. Link: {event_link}"

    def _update_event_with_token(
        self,
        token: str,
        event_id: str,
        existing_event: dict,
    ) -> Optional[str]:
        """Attempt to update an event using the given token. Returns None on 401/403."""
        import httpx

        with httpx.Client() as client:
            response = client.put(
                f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=existing_event,
            )

        if response.status_code in (401, 403):
            return None

        response.raise_for_status()
        return f"Updated event: '{existing_event.get('summary', event_id)}'"

    def _fetch_event_with_token(
        self,
        token: str,
        event_id: str,
    ) -> Optional[dict]:
        """Fetch an existing event. Returns None on 401/403, raises on 404."""
        import httpx

        with httpx.Client() as client:
            response = client.get(
                f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

        if response.status_code == 404:
            raise ValueError(f"Event not found: {event_id}")
        if response.status_code in (401, 403):
            return None

        response.raise_for_status()
        return response.json()

    # ── Public methods with system-first fallback ───────────────────────────────

    def list_calendar_events(
        self,
        start_date: str = None,
        end_date: str = None,
        max_results: int = 10,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None
    ) -> str:
        """
        List calendar events within a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format (defaults to today).
            end_date: End date in YYYY-MM-DD format (defaults to 7 days from start).
            max_results: Maximum number of events to return (default: 10).
            user_timezone: User's local timezone in IANA format (e.g., "America/Los_Angeles",
                "Europe/London"). Dates are interpreted in this timezone and converted to
                UTC for the Google Calendar API. Defaults to "UTC".

        Returns:
            Formatted list of events or access instructions if not connected.

        Note:
            - Requires user_id from tool_context (phone number).
            - The Google Calendar API requires times in UTC. This tool handles the
              conversion automatically based on the user_timezone parameter.
            - When the user expresses a time range (e.g., "today", "tomorrow"), the agent
              should determine their timezone and pass it here.
        """
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        try:
            user_tz = ZoneInfo(user_timezone)
        except KeyError:
            return f"Invalid timezone: {user_timezone}. Use IANA format (e.g., 'America/Los_Angeles')."

        try:
            if start_date:
                start_local = datetime.strptime(start_date, "%Y-%m-%d").replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=user_tz
                )
                start_dt = start_local.astimezone(timezone.utc)
            else:
                now_local = datetime.now(user_tz)
                start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                start_dt = start_local.astimezone(timezone.utc)

            if end_date:
                end_local = datetime.strptime(end_date, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, microsecond=0, tzinfo=user_tz
                )
                end_dt = end_local.astimezone(timezone.utc)
            else:
                end_dt = start_dt + timedelta(days=7)
        except ValueError as e:
            return f"Invalid date format. Please use YYYY-MM-DD. Error: {e}"

        try:
            sys_token = self._oauth_client.get_valid_access_token(
                CALENDAR_SYSTEM_USER_ID, CALENDAR_SYSTEM_FEATURE
            )
            if sys_token and _is_allowed_slack_user(phone_number, self._oauth_client):
                result = self._list_events_with_token(
                    sys_token, start_dt, end_dt, max_results, start_date, end_date
                )
                if result is not None:
                    return result

            user_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")
            if user_token:
                result = self._list_events_with_token(
                    user_token, start_dt, end_dt, max_results, start_date, end_date
                )
                if result is not None:
                    return result
        except Exception as e:
            return f"Error accessing calendar: {str(e)}"

        return _no_calendar_access(phone_number, self._oauth_client)

    def create_calendar_event(
        self,
        title: str,
        start: str,
        end: str = None,
        description: str = None,
        location: str = None,
        all_day: bool = False,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None
    ) -> str:
        """
        Create a new calendar event.

        Args:
            title: Event title/summary.
            start: Start datetime (YYYY-MM-DD for all-day, or YYYY-MM-DDTHH:MM for timed).
            end: End datetime (optional, defaults to 1 hour after start for timed events).
            description: Event description (optional).
            location: Event location (optional).
            all_day: Whether this is an all-day event (default: False).
            user_timezone: User's timezone in IANA format (e.g., "America/Los_Angeles").
                Used to interpret event times and set the event's timezone.

        Returns:
            Confirmation message or access instructions if not connected.

        Note: Requires user_id from tool_context (phone number).
        """
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Build event payload
        event = {"summary": title}
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        try:
            if all_day or len(start) == 10:
                event["start"] = {"date": start}
                event["end"] = {"date": end or start}
            else:
                if "T" not in start:
                    start = f"{start}T00:00:00"

                start_dt = datetime.fromisoformat(start)
                if end:
                    if "T" not in end:
                        end = f"{end}T23:59:00"
                    end_dt = datetime.fromisoformat(end)
                else:
                    end_dt = start_dt + timedelta(hours=1)

                event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": user_timezone}
                event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": user_timezone}

        except ValueError as e:
            return f"Invalid datetime format. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM. Error: {e}"

        try:
            sys_token = self._oauth_client.get_valid_access_token(
                CALENDAR_SYSTEM_USER_ID, CALENDAR_SYSTEM_FEATURE
            )
            if sys_token and _is_allowed_slack_user(phone_number, self._oauth_client):
                result = self._create_event_with_token(sys_token, event, title, start)
                if result is not None:
                    return result

            user_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")
            if user_token:
                result = self._create_event_with_token(user_token, event, title, start)
                if result is not None:
                    return result
        except Exception as e:
            return f"Error creating event: {str(e)}"

        return _no_calendar_access(phone_number, self._oauth_client)

    def update_calendar_event(
        self,
        event_id: str,
        title: str = None,
        start: str = None,
        end: str = None,
        description: str = None,
        location: str = None,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None
    ) -> str:
        """
        Update an existing calendar event.

        Args:
            event_id: The event ID to update (from list_calendar_events).
            title: New event title (optional).
            start: New start datetime (optional).
            end: New end datetime (optional).
            description: New description (optional).
            location: New location (optional).
            user_timezone: User's timezone in IANA format (e.g., "America/Los_Angeles").
                Used when updating event times.

        Returns:
            Confirmation message or error.

        Note: Requires user_id from tool_context (phone number).
        """
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Try to fetch the existing event with a working token, then update it
        try:
            raw_sys_token = self._oauth_client.get_valid_access_token(
                CALENDAR_SYSTEM_USER_ID, CALENDAR_SYSTEM_FEATURE
            )
            sys_token = raw_sys_token if _is_allowed_slack_user(phone_number, self._oauth_client) else None
            user_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")

            existing_event = None
            active_token = None

            for token in filter(None, [sys_token, user_token]):
                try:
                    fetched = self._fetch_event_with_token(token, event_id)
                    if fetched is not None:
                        existing_event = fetched
                        active_token = token
                        break
                except ValueError as e:
                    return str(e)  # 404 — event not found

            if existing_event is None or active_token is None:
                return _no_calendar_access(phone_number, self._oauth_client)

        except Exception as e:
            return f"Error fetching event: {str(e)}"

        # Apply updates to event payload
        if title:
            existing_event["summary"] = title
        if description:
            existing_event["description"] = description
        if location:
            existing_event["location"] = location

        try:
            if start:
                if len(start) == 10:
                    existing_event["start"] = {"date": start}
                else:
                    if "T" not in start:
                        start = f"{start}T00:00:00"
                    existing_event["start"] = {"dateTime": start, "timeZone": user_timezone}

            if end:
                if len(end) == 10:
                    existing_event["end"] = {"date": end}
                else:
                    if "T" not in end:
                        end = f"{end}T23:59:00"
                    existing_event["end"] = {"dateTime": end, "timeZone": user_timezone}

        except ValueError as e:
            return f"Invalid datetime format: {e}"

        try:
            result = self._update_event_with_token(active_token, event_id, existing_event)
            if result is not None:
                return result
        except Exception as e:
            return f"Error updating event: {str(e)}"

        return _no_calendar_access(phone_number, self._oauth_client)

    def get_calendar_status(self, tool_context: ToolContext = None) -> str:
        """
        Check if Google Calendar is connected for the user.

        Returns:
            Connection status (system account, personal account, or neither).

        Note: Requires user_id from tool_context (phone number).
        """
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot check calendar status - no user_id in tool_context."

        phone_number = tool_context.user_id
        lines = []

        sys_token = self._oauth_client.get_valid_access_token(
            CALENDAR_SYSTEM_USER_ID, CALENDAR_SYSTEM_FEATURE
        )
        if sys_token and _is_allowed_slack_user(phone_number, self._oauth_client):
            lines.append(f"System account ({AGENT_EMAIL}) is authorized for calendar access.")
        else:
            lines.append(f"System account ({AGENT_EMAIL}) is NOT authorized (or not a Slack workspace user).")

        user_token_data = self._oauth_client.get_token_data(phone_number, "calendar")
        if user_token_data:
            email = user_token_data.get("email", "Unknown")
            lines.append(f"Personal account ({email}) is authorized.")
        else:
            link = self._oauth_client.get_oauth_link(phone_number, "calendar")
            lines.append(f"Personal account is not connected. Authorize here:\n{link}")

        return "\n".join(lines)
