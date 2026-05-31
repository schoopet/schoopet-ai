"""Shared utilities for the Schoopet agent package."""
import re
from typing import Optional


_DRIVE_ID_PATTERNS = [
    re.compile(r"/document/d/([A-Za-z0-9_-]+)"),
    re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)"),
    re.compile(r"/presentation/d/([A-Za-z0-9_-]+)"),
    re.compile(r"/file/d/([A-Za-z0-9_-]+)"),
    re.compile(r"/(?:drive/)?folders/([A-Za-z0-9_-]+)"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]+)"),
]


def normalize_drive_id(value: str) -> str:
    """Return a bare Drive resource ID for common Drive URL inputs."""
    if not value or "/" not in value:
        return value

    for pattern in _DRIVE_ID_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group(1)
    return value


def doc_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


def sheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def drive_file_url(file_id: str, mime_type: str = "") -> str:
    if mime_type == "application/vnd.google-apps.document":
        return doc_url(file_id)
    if mime_type == "application/vnd.google-apps.spreadsheet":
        return sheet_url(file_id)
    if mime_type == "application/vnd.google-apps.presentation":
        return f"https://docs.google.com/presentation/d/{file_id}/edit"
    if mime_type == "application/vnd.google-apps.folder":
        return f"https://drive.google.com/drive/folders/{file_id}"
    return f"https://drive.google.com/file/d/{file_id}/view"


def normalize_user_id(user_id: str) -> str:
    """Normalize a user ID for consistent Firestore document IDs.

    Strips leading '+', dashes, and spaces so the same user is always mapped
    to the same document key regardless of how their ID was formatted.
    Non-phone user IDs pass through unchanged.

    Examples:
        "+1-949-413-6310" → "19494136310"
        "+19494136310"    → "19494136310"
        "U123456"         → "U123456"
        "discord:123"     → "discord:123"
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
