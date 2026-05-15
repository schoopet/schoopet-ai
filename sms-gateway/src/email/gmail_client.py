"""Gmail API helpers for the email processing module.

All functions are stateless — they accept an access token directly.
Token retrieval from OAuth storage is handled by get_gmail_token().

Constants shared with the agent side (agents/schoopet/email_tool.py):
    EMAIL_RULES_COLLECTION — must match the constant in email_tool.py
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# OAuth feature name for personal Gmail
PERSONAL_GMAIL_FEATURE = "google"

# Firestore collection for email rules — must match EMAIL_RULES_COLLECTION
# in agents/schoopet/email_tool.py (different package, cannot share import)
EMAIL_RULES_COLLECTION = "email_rules"


def normalize_gmail_address(gmail_address: str) -> str:
    """Return a Firestore-safe document ID for a Gmail address.

    Converts special characters so the address can be used directly as a
    Firestore document ID.

    Examples:
        "user@gmail.com"         -> "user_at_gmail_com"
        "foo.bar@example.org"    -> "foo_bar_at_example_org"
    """
    return gmail_address.lower().replace("@", "_at_").replace(".", "_")


async def get_gmail_token(user_id: str) -> Optional[str]:
    """Return a valid Gmail access token for user_id via IAM connector, or None."""
    from ..auth.connector import get_connector_token
    return await get_connector_token(user_id)


async def get_user_profile(token: str) -> Optional[dict]:
    """Fetch the authenticated user's Gmail profile (emailAddress, historyId).

    Returns:
        Dict with 'emailAddress' and 'historyId', or None on failure.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{GMAIL_API_BASE}/profile",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"users.getProfile failed: {e}")
            return None


async def setup_watch(token: str, topic_name: str) -> Optional[dict]:
    """Set up Gmail push notifications for the account owning the token.

    Args:
        token: A valid Gmail OAuth access token.
        topic_name: Full Pub/Sub topic name.

    Returns:
        Dict with 'historyId' and 'expiration' (ms epoch), or None on failure.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{GMAIL_API_BASE}/watch",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "labelIds": ["CATEGORY_PERSONAL"],
                    "topicName": topic_name,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Gmail watch set up, historyId={data.get('historyId')}")
            return data
        except Exception as e:
            logger.error(f"Gmail watch setup failed: {e}")
            return None


async def get_new_messages(
    token: str, start_history_id: str, new_history_id: str = ""
) -> list[dict]:
    """Fetch new messages since start_history_id using the given token.

    Args:
        token: A valid Gmail OAuth access token.
        start_history_id: Baseline historyId (exclusive lower bound).
        new_history_id: The historyId from the Pub/Sub notification (for logging).

    Returns:
        List of metadata dicts: {id, from, subject, snippet}
    """
    log_suffix = f" (notification historyId={new_history_id})" if new_history_id else ""
    logger.debug(f"history.list startHistoryId={start_history_id}{log_suffix}")

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{GMAIL_API_BASE}/history",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "startHistoryId": start_history_id,
                    "historyTypes": "messageAdded",
                    "labelId": "CATEGORY_PERSONAL",
                },
            )
            if resp.status_code == 404:
                logger.warning("historyId expired, falling back to recent messages")
                return await _get_recent_messages(token, client)

            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"history.list failed: {e}")
            return []

        message_ids: list[str] = []
        for record in data.get("history", []):
            for added in record.get("messagesAdded", []):
                msg_id = added.get("message", {}).get("id")
                if msg_id:
                    message_ids.append(msg_id)

        emails = []
        for msg_id in message_ids:
            metadata = await _fetch_metadata(msg_id, token, client)
            if metadata:
                emails.append(metadata)
        return emails


async def _get_recent_messages(
    token: str, client: httpx.AsyncClient, max_results: int = 10
) -> list[dict]:
    """Fallback: list recent INBOX messages when historyId has expired."""
    try:
        resp = await client.get(
            f"{GMAIL_API_BASE}/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"labelIds": "CATEGORY_PERSONAL", "maxResults": max_results},
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
    except Exception as e:
        logger.error(f"messages.list failed: {e}")
        return []

    emails = []
    for msg in messages:
        metadata = await _fetch_metadata(msg["id"], token, client)
        if metadata:
            emails.append(metadata)
    return emails


async def _fetch_metadata(
    message_id: str,
    token: str,
    client: httpx.AsyncClient,
) -> Optional[dict]:
    """Fetch From/Subject/snippet for a single message."""
    try:
        resp = await client.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date"],
            },
        )
        resp.raise_for_status()
        msg = resp.json()
        headers = msg.get("payload", {}).get("headers", [])
        return {
            "id": msg.get("id", ""),
            "from": _get_header(headers, "From"),
            "subject": _get_header(headers, "Subject"),
            "snippet": msg.get("snippet", ""),
        }
    except Exception as e:
        logger.error(f"messages.get({message_id}) metadata failed: {e}")
        return None


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""
