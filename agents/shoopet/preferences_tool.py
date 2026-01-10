"""User preferences tool for agent - stores user settings like timezone."""
import os
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo
from google.adk.tools import ToolContext

# Firestore collection for user preferences
PREFERENCES_COLLECTION = "user_preferences"


class PreferencesTool:
    """Tool for managing user preferences stored in Firestore.

    Stores user-specific settings like timezone that can be used by
    other tools (e.g., CalendarTool) to provide better context.
    """

    def __init__(self):
        """Initialize the Preferences Tool - all initialization is deferred."""
        self._firestore_client = None
        self._initialized = False
        self._project_id = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration."""
        if self._initialized:
            return
        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
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

    def _normalize_phone(self, phone_number: str) -> str:
        """Normalize phone number for consistent document IDs."""
        return phone_number.lstrip("+").replace("-", "").replace(" ", "")

    def _get_preferences(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """Get all preferences for a user from Firestore."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return None

        normalized = self._normalize_phone(phone_number)
        doc_ref = firestore_client.collection(PREFERENCES_COLLECTION).document(normalized)
        doc = doc_ref.get()

        if doc.exists:
            return doc.to_dict()
        return None

    def _save_preference(self, phone_number: str, key: str, value: Any) -> bool:
        """Save a single preference for a user."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return False

        normalized = self._normalize_phone(phone_number)
        doc_ref = firestore_client.collection(PREFERENCES_COLLECTION).document(normalized)

        from datetime import datetime, timezone
        doc_ref.set({
            key: value,
            "updated_at": datetime.now(timezone.utc),
            "phone_number": phone_number,
        }, merge=True)
        return True

    def set_timezone(
        self,
        timezone_str: str,
        tool_context: ToolContext = None
    ) -> str:
        """
        Set the user's preferred timezone.

        Args:
            timezone_str: Timezone in IANA format (e.g., "America/Los_Angeles",
                "America/New_York", "Europe/London", "Asia/Tokyo").

        Returns:
            Confirmation message or error.

        Note:
            - Requires user_id from tool_context (phone number).
            - The timezone will be used automatically by calendar tools.

        Common timezone examples:
            - US Pacific: "America/Los_Angeles"
            - US Mountain: "America/Denver"
            - US Central: "America/Chicago"
            - US Eastern: "America/New_York"
            - UK: "Europe/London"
            - Central Europe: "Europe/Paris"
            - Japan: "Asia/Tokyo"
            - Australia Eastern: "Australia/Sydney"
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot save preference - no user_id in tool_context."

        phone_number = tool_context.user_id

        # Validate timezone
        try:
            ZoneInfo(timezone_str)
        except KeyError:
            return (
                f"Invalid timezone: '{timezone_str}'. "
                f"Please use IANA timezone format (e.g., 'America/Los_Angeles', 'America/New_York', 'Europe/London')."
            )

        # Save timezone preference
        success = self._save_preference(phone_number, "timezone", timezone_str)
        if success:
            return f"Timezone set to {timezone_str}. Calendar events will now use this timezone."
        else:
            return "ERROR: Could not save timezone preference. Please try again."

    def get_timezone(
        self,
        tool_context: ToolContext = None
    ) -> str:
        """
        Get the user's preferred timezone.

        Returns:
            The user's timezone string (e.g., "America/Los_Angeles") or a message
            indicating no timezone is set.

        Note: Requires user_id from tool_context (phone number).
        """
        # Validate tool_context
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot get preference - no user_id in tool_context."

        phone_number = tool_context.user_id

        preferences = self._get_preferences(phone_number)
        if preferences and "timezone" in preferences:
            return f"User timezone: {preferences['timezone']}"

        return "No timezone set. Ask the user for their timezone and use set_timezone to save it."

    def get_timezone_value(self, phone_number: str) -> Optional[str]:
        """
        Get the user's timezone value directly (for use by other tools).

        This method is for internal use by other tools like CalendarTool
        to retrieve the timezone without going through the agent.

        Args:
            phone_number: The user's phone number.

        Returns:
            The timezone string if set, None otherwise.
        """
        preferences = self._get_preferences(phone_number)
        if preferences and "timezone" in preferences:
            return preferences["timezone"]
        return None
