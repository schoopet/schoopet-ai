"""Google Calendar tool for agent using OAuth tokens.

Uses the calling user's personal OAuth token (feature="google").
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .oauth_client import OAuthClient
from .utils import require_user_id

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarTool:
    """Tool for accessing Google Calendar via the user's personal OAuth token."""

    def __init__(self):
        self._oauth_client = OAuthClient()

    def _get_service(self, user_id: str):
        credentials = self._oauth_client.get_google_credentials(
            user_id, "google", scopes=CALENDAR_SCOPES
        )
        if credentials is None:
            return None, self._oauth_client.get_oauth_link(user_id)

        service = build(
            "calendar",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        return service, None

    def _format_event(self, event: Dict[str, Any]) -> str:
        """Format a calendar event for display."""
        event_id = event.get("id", "")
        summary = event.get("summary", "No title")
        start = event.get("start", {})
        end = event.get("end", {})

        if "date" in start:
            start_str = start["date"]
            end_str = end.get("date", "")
            time_str = f"{start_str}" + (
                f" to {end_str}" if end_str != start_str else " (all day)"
            )
        else:
            start_dt = start.get("dateTime", "")
            end_dt = end.get("dateTime", "")
            try:
                start_parsed = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                end_parsed = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                time_str = (
                    f"{start_parsed.strftime('%b %d, %I:%M %p')} - "
                    f"{end_parsed.strftime('%I:%M %p')}"
                )
            except Exception:
                time_str = f"{start_dt} - {end_dt}"

        location = event.get("location", "")

        result = f"- {summary}: {time_str}"
        if location:
            result += f" @ {location}"
        if event_id:
            result += f" [ID: {event_id}]"

        return result

    def _is_auth_error(self, error: HttpError) -> bool:
        return getattr(error.resp, "status", None) in (401, 403)

    def list_calendar_events(
        self,
        start_date: str = None,
        end_date: str = None,
        max_results: int = 10,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None,
    ) -> str:
        """List calendar events within a date range."""
        user_id, err = require_user_id(tool_context, "google")
        if err:
            return err

        try:
            user_tz = ZoneInfo(user_timezone)
        except KeyError:
            return (
                f"Invalid timezone: {user_timezone}. "
                "Use IANA format (e.g., 'America/Los_Angeles')."
            )

        try:
            if start_date:
                start_local = datetime.strptime(start_date, "%Y-%m-%d").replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=user_tz
                )
                start_dt = start_local.astimezone(timezone.utc)
            else:
                now_local = datetime.now(user_tz)
                start_local = now_local.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
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

        service, auth_link = self._get_service(user_id)
        if service is None:
            return auth_link

        try:
            response = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=start_dt.isoformat(),
                    timeMax=end_dt.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = response.get("items", [])
            if not events:
                return (
                    f"No events found between {start_date or 'today'} and "
                    f"{end_date or '7 days from now'}."
                )

            formatted_events = [self._format_event(event) for event in events]
            return f"Found {len(events)} event(s):\n" + "\n".join(formatted_events)
        except HttpError as e:
            if self._is_auth_error(e):
                return auth_link
            return f"Error accessing calendar: {e}"
        except Exception as e:
            return f"Error accessing calendar: {e}"

    def create_calendar_event(
        self,
        title: str,
        start: str,
        end: str = None,
        description: str = None,
        location: str = None,
        all_day: bool = False,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None,
    ) -> str:
        """Create a new calendar event."""
        user_id, err = require_user_id(tool_context, "google")
        if err:
            return err

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

                event["start"] = {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": user_timezone,
                }
                event["end"] = {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": user_timezone,
                }
        except ValueError as e:
            return (
                "Invalid datetime format. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM. "
                f"Error: {e}"
            )

        service, auth_link = self._get_service(user_id)
        if service is None:
            return auth_link

        try:
            created_event = (
                service.events()
                .insert(calendarId="primary", body=event)
                .execute()
            )
            event_link = created_event.get("htmlLink", "")
            return f"Created event: '{title}' on {start}. Link: {event_link}"
        except HttpError as e:
            if self._is_auth_error(e):
                return auth_link
            return f"Error creating event: {e}"
        except Exception as e:
            return f"Error creating event: {e}"

    def update_calendar_event(
        self,
        event_id: str,
        title: str = None,
        start: str = None,
        end: str = None,
        description: str = None,
        location: str = None,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None,
    ) -> str:
        """Update an existing calendar event."""
        user_id, err = require_user_id(tool_context, "google")
        if err:
            return err

        service, auth_link = self._get_service(user_id)
        if service is None:
            return auth_link

        try:
            existing_event = (
                service.events()
                .get(calendarId="primary", eventId=event_id)
                .execute()
            )
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status == 404:
                return f"Event not found: {event_id}"
            if self._is_auth_error(e):
                return auth_link
            return f"Error fetching event: {e}"
        except Exception as e:
            return f"Error fetching event: {e}"

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
                    existing_event["start"] = {
                        "dateTime": start,
                        "timeZone": user_timezone,
                    }

            if end:
                if len(end) == 10:
                    existing_event["end"] = {"date": end}
                else:
                    if "T" not in end:
                        end = f"{end}T23:59:00"
                    existing_event["end"] = {
                        "dateTime": end,
                        "timeZone": user_timezone,
                    }
        except ValueError as e:
            return f"Invalid datetime format: {e}"

        try:
            (
                service.events()
                .update(calendarId="primary", eventId=event_id, body=existing_event)
                .execute()
            )
            return f"Updated event: '{existing_event.get('summary', event_id)}'"
        except HttpError as e:
            if self._is_auth_error(e):
                return auth_link
            return f"Error updating event: {e}"
        except Exception as e:
            return f"Error updating event: {e}"

    def get_calendar_status(self, tool_context: ToolContext = None) -> str:
        """Check if Google Calendar is connected."""
        user_id, err = require_user_id(tool_context, "google")
        if err:
            return err

        user_token_data = self._oauth_client.get_token_data(user_id, "google")
        if user_token_data:
            email = user_token_data.get("email", "Unknown")
            user_access_token = self._oauth_client.get_valid_access_token(
                user_id, "google"
            )
            if user_access_token:
                return f"Calendar ({email}) is connected."
            link = self._oauth_client.get_oauth_link(user_id)
            return f"Calendar ({email}) connection has expired. Re-authorize:\n{link}"

        link = self._oauth_client.get_oauth_link(user_id)
        return f"Calendar is not connected. Authorize here:\n{link}"
