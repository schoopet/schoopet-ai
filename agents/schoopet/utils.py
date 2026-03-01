"""Shared utilities for the Schoopet agent package."""
from typing import Optional


def normalize_user_id(user_id: str) -> str:
    """Normalize a user ID for consistent Firestore document IDs.

    Strips leading '+', dashes, and spaces so the same user is always mapped
    to the same document key regardless of how their ID was formatted.
    Non-phone user IDs (Slack IDs, Telegram IDs, etc.) pass through unchanged.

    Examples:
        "+1-949-413-6310" → "19494136310"
        "+19494136310"    → "19494136310"
        "U123456"         → "U123456"
        "email_system"    → "email_system"
    """
    return user_id.lstrip("+").replace("-", "").replace(" ", "")


def require_user_id(
    tool_context, tool_name: str = "this tool"
) -> "tuple[Optional[str], Optional[str]]":
    """Validate that tool_context carries a user_id.

    Returns:
        (user_id, None) on success.
        (None, error_message) when user_id is missing.
    """
    user_id = getattr(tool_context, "user_id", None) if tool_context else None
    if not user_id:
        return None, f"ERROR: Cannot use {tool_name} — no user_id in tool context."
    return user_id, None
