"""Shared utilities for the SMS gateway."""


def normalize_user_id(user_id: str) -> str:
    """Normalize a user ID for consistent Firestore document IDs.

    Strips leading '+', dashes, and spaces so the same user is always mapped
    to the same document key regardless of how their ID was formatted.

    Examples:
        "+1-949-413-6310" → "19494136310"
        "+19494136310"    → "19494136310"
    """
    return user_id.lstrip("+").replace("-", "").replace(" ", "")
