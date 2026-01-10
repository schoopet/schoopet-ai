"""Google Calendar tool for agent using OAuth tokens."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo
from google.adk.tools import ToolContext
from .oauth_client import OAuthClient

# Google Calendar API endpoints
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class CalendarTool:
    """Tool for accessing Google Calendar via user OAuth tokens.

    This tool fetches OAuth tokens stored by the SMS Gateway and uses them
    to access the user's Google Calendar. If no token exists, it returns
    a link for the user to authorize access.
    """

    def __init__(self):
        """Initialize the Calendar Tool."""
        self._oauth_client = OAuthClient()

    def _format_event(self, event: Dict[str, Any]) -> str:
        """Format a calendar event for display."""
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
            # Parse and format nicely
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

        return result

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
            Formatted list of events or authorization link if not connected.

        Note:
            - Requires user_id from tool_context (phone number).
            - The Google Calendar API requires times in UTC. This tool handles the
              conversion automatically based on the user_timezone parameter.
            - When the user expresses a time range (e.g., "today", "tomorrow"), the agent
              should determine their timezone and pass it here.
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Check for valid token
        access_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")
        if not access_token:
            oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
            return (
                f"Your Google Calendar is not connected yet. "
                f"Please authorize access by visiting:\n{oauth_link}\n\n"
                f"After authorizing, try again."
            )

        # Validate and get user timezone
        try:
            user_tz = ZoneInfo(user_timezone)
        except KeyError:
            return f"Invalid timezone: {user_timezone}. Use IANA format (e.g., 'America/Los_Angeles')."

        # Parse dates in user's local timezone, then convert to UTC for the Calendar API
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

        # Call Calendar API
        try:
            # Import here to avoid issues during pickling
            import httpx

            with httpx.Client() as client:
                response = client.get(
                    f"{CALENDAR_API_BASE}/calendars/primary/events",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "timeMin": start_dt.isoformat(),
                        "timeMax": end_dt.isoformat(),
                        "maxResults": max_results,
                        "singleEvents": "true",
                        "orderBy": "startTime",
                    },
                )

                if response.status_code == 401:
                    # Token might be revoked
                    oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
                    return (
                        f"Your calendar authorization has expired. "
                        f"Please re-authorize by visiting:\n{oauth_link}"
                    )

                response.raise_for_status()
                data = response.json()

            events = data.get("items", [])
            if not events:
                return f"No events found between {start_date or 'today'} and {end_date or '7 days from now'}."

            formatted_events = [self._format_event(event) for event in events]
            return f"Found {len(events)} event(s):\n" + "\n".join(formatted_events)

        except Exception as e:
            return f"Error accessing calendar: {str(e)}"

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
            Confirmation message or authorization link if not connected.

        Note: Requires user_id from tool_context (phone number).
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Check for valid token
        access_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")
        if not access_token:
            oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
            return (
                f"Your Google Calendar is not connected yet. "
                f"Please authorize access by visiting:\n{oauth_link}\n\n"
                f"After authorizing, try again."
            )

        # Build event payload
        event = {"summary": title}

        if description:
            event["description"] = description
        if location:
            event["location"] = location

        try:
            if all_day or len(start) == 10:  # YYYY-MM-DD format
                # All-day event
                event["start"] = {"date": start}
                event["end"] = {"date": end or start}
            else:
                # Timed event
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

        # Create event via API
        try:
            # Import here to avoid issues during pickling
            import httpx

            with httpx.Client() as client:
                response = client.post(
                    f"{CALENDAR_API_BASE}/calendars/primary/events",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=event,
                )

                if response.status_code == 401:
                    oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
                    return (
                        f"Your calendar authorization has expired. "
                        f"Please re-authorize by visiting:\n{oauth_link}"
                    )

                response.raise_for_status()
                created_event = response.json()

            event_link = created_event.get("htmlLink", "")
            return f"Created event: '{title}' on {start}. Link: {event_link}"

        except Exception as e:
            return f"Error creating event: {str(e)}"

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
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Check for valid token
        access_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")
        if not access_token:
            oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
            return (
                f"Your Google Calendar is not connected yet. "
                f"Please authorize access by visiting:\n{oauth_link}\n\n"
                f"After authorizing, try again."
            )

        # Import here to avoid issues during pickling
        import httpx

        # First, get the existing event
        try:
            with httpx.Client() as client:
                response = client.get(
                    f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                if response.status_code == 404:
                    return f"Event not found: {event_id}"
                if response.status_code == 401:
                    oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
                    return f"Authorization expired. Please re-authorize: {oauth_link}"

                response.raise_for_status()
                existing_event = response.json()

        except Exception as e:
            return f"Error fetching event: {str(e)}"

        # Update fields
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

        # Update event via API
        try:
            with httpx.Client() as client:
                response = client.put(
                    f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=existing_event,
                )

                if response.status_code == 401:
                    oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
                    return f"Authorization expired. Please re-authorize: {oauth_link}"

                response.raise_for_status()

            return f"Updated event: '{existing_event.get('summary', event_id)}'"

        except Exception as e:
            return f"Error updating event: {str(e)}"

    def get_calendar_status(self, tool_context: ToolContext = None) -> str:
        """
        Check if Google Calendar is connected for the user.

        Returns:
            Connection status and email if connected, or authorization link if not.

        Note: Requires user_id from tool_context (phone number).
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot check calendar status - no user_id in tool_context."

        phone_number = tool_context.user_id

        token_data = self._oauth_client.get_token_data(phone_number, "calendar")
        if not token_data:
            oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
            return (
                f"Google Calendar is not connected. "
                f"To connect, visit:\n{oauth_link}"
            )

        email = token_data.get("email", "Unknown")
        is_expired = self._oauth_client.is_token_expired(token_data)

        if is_expired:
            # Try to refresh
            access_token = self._oauth_client.get_valid_access_token(phone_number, "calendar")
            if not access_token:
                oauth_link = self._oauth_client.get_oauth_link(phone_number, "calendar")
                return (
                    f"Google Calendar connection has expired. "
                    f"Please re-authorize:\n{oauth_link}"
                )

        return f"Google Calendar is connected to: {email}"
