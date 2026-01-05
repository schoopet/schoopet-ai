"""Google Calendar tool for agent using OAuth tokens."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext

# Firestore collection for OAuth tokens (shared with SMS gateway)
TOKENS_COLLECTION = "oauth_tokens"

# Google Calendar API endpoints
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class CalendarTool:
    """Tool for accessing Google Calendar via user OAuth tokens.

    This tool fetches OAuth tokens stored by the SMS Gateway and uses them
    to access the user's Google Calendar. If no token exists, it returns
    a link for the user to authorize access.

    All clients are lazily initialized on first use for pickling support.
    """

    def __init__(self):
        """Initialize the Calendar Tool - all initialization is deferred."""
        # All client instances are None until first use
        self._firestore_client = None
        self._secret_client = None
        self._initialized = False

        # Configuration - will be loaded lazily
        self._project_id = None
        self._oauth_base_url = None
        self._client_id = None
        self._client_secret = None

        # HMAC secret for OAuth initiation tokens (cached)
        self._hmac_secret = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration from environment variables."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._oauth_base_url = os.getenv("OAUTH_BASE_URL", "")
        self._client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        self._client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
        self._initialized = True

    def _get_firestore_client(self):
        """Get Firestore client, initializing lazily."""
        if self._firestore_client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=self._project_id)
        return self._firestore_client

    def _get_secret_client(self):
        """Get Secret Manager client, initializing lazily."""
        if self._secret_client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import secretmanager
                self._secret_client = secretmanager.SecretManagerServiceClient()
        return self._secret_client

    def _get_hmac_secret(self) -> Optional[str]:
        """Get HMAC secret from Secret Manager (cached)."""
        if self._hmac_secret is not None:
            return self._hmac_secret

        self._ensure_initialized()
        secret_client = self._get_secret_client()
        if not secret_client or not self._project_id:
            return None

        try:
            secret_name = f"projects/{self._project_id}/secrets/oauth-hmac-secret/versions/latest"
            response = secret_client.access_secret_version(request={"name": secret_name})
            self._hmac_secret = response.payload.data.decode("UTF-8")
            return self._hmac_secret
        except Exception as e:
            print(f"Error getting HMAC secret: {e}")
            return None

    def _normalize_phone(self, phone_number: str) -> str:
        """Normalize phone number for consistent document IDs."""
        return phone_number.lstrip("+").replace("-", "").replace(" ", "")

    def _get_oauth_link(self, phone_number: str) -> str:
        """Generate secure HMAC-signed OAuth authorization link."""
        self._ensure_initialized()

        if not self._oauth_base_url:
            return "Calendar authorization not configured. Please contact support."

        secret = self._get_hmac_secret()
        if not secret:
            return "Calendar authorization temporarily unavailable. Please try again later."

        # Generate HMAC-signed token
        # Import here to avoid issues during pickling
        import hmac as hmac_lib
        import hashlib
        import base64
        import time

        # Clean phone number to E.164 format (strip spaces, dashes, parens)
        # The SMS gateway expects strict E.164 format: ^\+[1-9]\d{1,14}$
        import re
        clean_phone = re.sub(r"[^0-9+]", "", phone_number)
        if not clean_phone.startswith("+"):
            clean_phone = "+" + clean_phone

        expires = int(time.time()) + 600  # 10 minutes
        message = f"{clean_phone}:{expires}"

        signature = hmac_lib.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        token = base64.urlsafe_b64encode(f"{message}:{signature}".encode()).decode()

        return f"{self._oauth_base_url}/oauth/google/initiate?token={token}"

    def _get_token(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """Get OAuth token from Firestore."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return None

        doc_id = self._normalize_phone(phone_number)
        doc_ref = firestore_client.collection(TOKENS_COLLECTION).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            return None

        return doc.to_dict()

    def _is_token_expired(self, token_data: Dict[str, Any]) -> bool:
        """Check if access token is expired."""
        expires_at = token_data.get("expires_at")
        if not expires_at:
            return True

        # Add 60 second buffer
        now = datetime.now(timezone.utc)
        if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        return now >= (expires_at - timedelta(seconds=60))

    def _get_refresh_token(self, phone_number: str) -> Optional[str]:
        """Get refresh token from Secret Manager."""
        self._ensure_initialized()
        secret_client = self._get_secret_client()
        if not secret_client:
            return None

        try:
            # Import here to avoid issues during pickling
            from google.api_core import exceptions

            normalized = self._normalize_phone(phone_number)
            secret_name = f"projects/{self._project_id}/secrets/oauth-refresh-{normalized}/versions/latest"
            response = secret_client.access_secret_version(request={"name": secret_name})
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            # Handle NotFound and other exceptions
            if "NotFound" in str(type(e).__name__):
                return None
            print(f"Error getting refresh token: {e}")
            return None

    def _refresh_access_token(self, phone_number: str, refresh_token: str) -> Optional[str]:
        """Refresh an expired access token."""
        self._ensure_initialized()
        if not self._client_id or not self._client_secret:
            return None

        try:
            # Import here to avoid issues during pickling
            import httpx

            with httpx.Client() as client:
                response = client.post(
                    TOKEN_URL,
                    data={
                        "refresh_token": refresh_token,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                token_data = response.json()

                access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)

                if access_token:
                    # Update token in Firestore
                    firestore_client = self._get_firestore_client()
                    if firestore_client:
                        doc_id = self._normalize_phone(phone_number)
                        doc_ref = firestore_client.collection(TOKENS_COLLECTION).document(doc_id)
                        now = datetime.now(timezone.utc)
                        doc_ref.update({
                            "access_token": access_token,
                            "expires_at": now + timedelta(seconds=expires_in),
                            "updated_at": now,
                        })
                    return access_token

                return None
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return None

    def _get_valid_access_token(self, phone_number: str) -> Optional[str]:
        """Get a valid access token, refreshing if necessary."""
        token_data = self._get_token(phone_number)
        if not token_data:
            return None

        access_token = token_data.get("access_token")

        # Check if expired
        if self._is_token_expired(token_data):
            # Try to refresh
            refresh_token = self._get_refresh_token(phone_number)
            if refresh_token:
                access_token = self._refresh_access_token(phone_number, refresh_token)

        return access_token

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
        tool_context: ToolContext = None
    ) -> str:
        """
        List calendar events within a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format (defaults to today).
            end_date: End date in YYYY-MM-DD format (defaults to 7 days from start).
            max_results: Maximum number of events to return (default: 10).

        Returns:
            Formatted list of events or authorization link if not connected.

        Note: Requires user_id from tool_context (phone number).
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Check for valid token
        access_token = self._get_valid_access_token(phone_number)
        if not access_token:
            oauth_link = self._get_oauth_link(phone_number)
            return (
                f"Your Google Calendar is not connected yet. "
                f"Please authorize access by visiting:\n{oauth_link}\n\n"
                f"After authorizing, try again."
            )

        # Parse dates
        try:
            if start_date:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                start_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

            if end_date:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                end_dt = end_dt.replace(hour=23, minute=59, second=59)
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
                    oauth_link = self._get_oauth_link(phone_number)
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

        Returns:
            Confirmation message or authorization link if not connected.

        Note: Requires user_id from tool_context (phone number).
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Check for valid token
        access_token = self._get_valid_access_token(phone_number)
        if not access_token:
            oauth_link = self._get_oauth_link(phone_number)
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

                event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "UTC"}
                event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "UTC"}

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
                    oauth_link = self._get_oauth_link(phone_number)
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

        Returns:
            Confirmation message or error.

        Note: Requires user_id from tool_context (phone number).
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access calendar - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Check for valid token
        access_token = self._get_valid_access_token(phone_number)
        if not access_token:
            oauth_link = self._get_oauth_link(phone_number)
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
                    oauth_link = self._get_oauth_link(phone_number)
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
                    existing_event["start"] = {"dateTime": start, "timeZone": "UTC"}

            if end:
                if len(end) == 10:
                    existing_event["end"] = {"date": end}
                else:
                    if "T" not in end:
                        end = f"{end}T23:59:00"
                    existing_event["end"] = {"dateTime": end, "timeZone": "UTC"}

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
                    oauth_link = self._get_oauth_link(phone_number)
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

        token_data = self._get_token(phone_number)
        if not token_data:
            oauth_link = self._get_oauth_link(phone_number)
            return (
                f"Google Calendar is not connected. "
                f"To connect, visit:\n{oauth_link}"
            )

        email = token_data.get("email", "Unknown")
        is_expired = self._is_token_expired(token_data)

        if is_expired:
            # Try to refresh
            access_token = self._get_valid_access_token(phone_number)
            if not access_token:
                oauth_link = self._get_oauth_link(phone_number)
                return (
                    f"Google Calendar connection has expired. "
                    f"Please re-authorize:\n{oauth_link}"
                )

        return f"Google Calendar is connected to: {email}"
