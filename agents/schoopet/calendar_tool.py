"""Google Calendar tool for agent using IAM connector credentials."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import require_user_id


class CalendarTool:
    """Tool for accessing Google Calendar via IAM connector credentials."""

    async def _get_service(self, tool_context: ToolContext):
        from .gcp_auth import get_workspace_service
        return await get_workspace_service("calendar", "v3", "calendar", tool_context)

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

        attendees = event.get("attendees", [])
        if attendees:
            emails = [a["email"] for a in attendees if "email" in a]
            if emails:
                result += f" | attendees: {', '.join(emails)}"

        for ep in event.get("conferenceData", {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                result += f" | meet: {ep.get('uri', '')}"
                break

        if event.get("recurrence"):
            result += " [recurring]"

        if event_id:
            result += f" [ID: {event_id}]"

        return result

    async def list_calendar_events(
        self,
        start_date: str = None,
        end_date: str = None,
        max_results: int = 10,
        user_timezone: str = "UTC",
        tool_context: ToolContext = None,
    ) -> str:
        """List calendar events within a date range."""
        _, err = require_user_id(tool_context, "google")
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

        service = await self._get_service(tool_context)
        if service is None:
            return ""

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
            return f"Error accessing calendar: {e}"
        except Exception as e:
            return f"Error accessing calendar: {e}"

    async def create_calendar_event(
        self,
        title: str,
        start: str,
        end: str = None,
        description: str = None,
        location: str = None,
        all_day: bool = False,
        user_timezone: str = "UTC",
        attendees: list = None,
        add_google_meet: bool = False,
        reminders: list = None,
        recurrence: str = None,
        color_id: int = None,
        visibility: str = None,
        transparency: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Create a new calendar event."""
        _, err = require_user_id(tool_context, "google")
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

        if attendees:
            event["attendees"] = [{"email": e} for e in attendees]
        if add_google_meet:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        if reminders is not None:
            event["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in reminders],
            }
        if recurrence:
            event["recurrence"] = [recurrence]
        if color_id is not None:
            event["colorId"] = str(color_id)
        if visibility:
            event["visibility"] = visibility
        if transparency:
            event["transparency"] = transparency

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            insert_kwargs = {"calendarId": "primary", "body": event}
            if attendees:
                insert_kwargs["sendUpdates"] = "all"
            if add_google_meet:
                insert_kwargs["conferenceDataVersion"] = 1
            created_event = service.events().insert(**insert_kwargs).execute()

            event_link = created_event.get("htmlLink", "")
            result = f"Created event: '{title}' on {start}. Link: {event_link}"
            for ep in created_event.get("conferenceData", {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    result += f" Meet: {ep['uri']}"
                    break
            return result
        except HttpError as e:
            return f"Error creating event: {e}"
        except Exception as e:
            return f"Error creating event: {e}"

    async def update_calendar_event(
        self,
        event_id: str,
        title: str = None,
        start: str = None,
        end: str = None,
        description: str = None,
        location: str = None,
        user_timezone: str = "UTC",
        attendees: list = None,
        reminders: list = None,
        recurrence: str = None,
        color_id: int = None,
        visibility: str = None,
        transparency: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Update an existing calendar event."""
        _, err = require_user_id(tool_context, "google")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

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

        if attendees is not None:
            existing_event["attendees"] = [{"email": e} for e in attendees]
        if reminders is not None:
            existing_event["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in reminders],
            }
        if recurrence is not None:
            existing_event["recurrence"] = [recurrence]
        if color_id is not None:
            existing_event["colorId"] = str(color_id)
        if visibility is not None:
            existing_event["visibility"] = visibility
        if transparency is not None:
            existing_event["transparency"] = transparency

        try:
            update_kwargs = {
                "calendarId": "primary",
                "eventId": event_id,
                "body": existing_event,
            }
            if attendees is not None:
                update_kwargs["sendUpdates"] = "all"
            service.events().update(**update_kwargs).execute()
            return f"Updated event: '{existing_event.get('summary', event_id)}'"
        except HttpError as e:
            return f"Error updating event: {e}"
        except Exception as e:
            return f"Error updating event: {e}"

    async def delete_calendar_event(
        self,
        event_id: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Delete a calendar event by ID."""
        _, err = require_user_id(tool_context, "google")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            service.events().delete(calendarId="primary", eventId=event_id).execute()
            return f"Deleted event: {event_id}"
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status == 404:
                return f"Event not found: {event_id}"
            return f"Error deleting event: {e}"
        except Exception as e:
            return f"Error deleting event: {e}"

    async def get_calendar_status(self, tool_context: ToolContext = None) -> str:
        """Check if Google Calendar is connected."""
        _, err = require_user_id(tool_context, "google")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""
        return "Google Calendar is connected."
