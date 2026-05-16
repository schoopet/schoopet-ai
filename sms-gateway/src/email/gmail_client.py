"""Gmail helpers shared between email processing modules."""
import logging

logger = logging.getLogger(__name__)

# Firestore collection for email rules — must match EMAIL_RULES_COLLECTION
# in agents/schoopet/email_tool.py (different package, cannot share import)
EMAIL_RULES_COLLECTION = "email_rules"


def normalize_gmail_address(gmail_address: str) -> str:
    """Return a Firestore-safe document ID for a Gmail address.

    Examples:
        "user@gmail.com"   -> "user_at_gmail_com"
        "foo.bar@corp.org" -> "foo_bar_at_corp_org"
    """
    return gmail_address.lower().replace("@", "_at_").replace(".", "_")
