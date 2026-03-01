"""Gmail API client for the email processing module.

Uses the system workspace_system OAuth token (stored under phone key "email_system",
feature "workspace_system") to read the dedicated inbox.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Fixed identifiers for the system Gmail account
SYSTEM_PHONE = "email_system"
SYSTEM_FEATURE = "workspace_system"


class GmailClient:
    """Read-only Gmail API client backed by the system OAuth token."""

    def __init__(self, oauth_manager):
        """
        Args:
            oauth_manager: OAuthManager instance (from sms-gateway) used to
                           retrieve a valid access token for the system account.
        """
        self._oauth_manager = oauth_manager

    async def _get_token(self) -> Optional[str]:
        """Return a valid system access token, or None."""
        return await self._oauth_manager.get_access_token(SYSTEM_PHONE, SYSTEM_FEATURE)

    async def get_new_messages(
        self, start_history_id: str, new_history_id: str = ""
    ) -> list[dict]:
        """Fetch new messages using Gmail history.list since start_history_id.

        Args:
            start_history_id: Previously stored baseline historyId used as
                              ``startHistoryId`` in the API call. Gmail returns
                              records *strictly after* this value, so passing
                              the notification's own historyId would always
                              yield nothing.
            new_history_id: The historyId from the Pub/Sub notification.
                            Used for logging only.

        Returns a list of metadata dicts:
            {id, from, subject, snippet}
        """
        token = await self._get_token()
        if not token:
            logger.error("No workspace_system token available — cannot fetch messages")
            return []

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
                    },
                )
                if resp.status_code == 404:
                    # historyId too old — fall back to listing recent messages
                    logger.warning("historyId expired, falling back to recent messages")
                    return await self._get_recent_messages(token, client)

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
                metadata = await self._fetch_metadata(msg_id, token=token, client=client)
                if metadata:
                    emails.append(metadata)
            return emails

    async def _get_recent_messages(
        self, token: str, client: httpx.AsyncClient, max_results: int = 10
    ) -> list[dict]:
        """Fallback: list recent messages from INBOX (metadata only)."""
        try:
            resp = await client.get(
                f"{GMAIL_API_BASE}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"labelIds": "INBOX", "maxResults": max_results},
            )
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
        except Exception as e:
            logger.error(f"messages.list failed: {e}")
            return []

        emails = []
        for msg in messages:
            metadata = await self._fetch_metadata(msg["id"], token=token, client=client)
            if metadata:
                emails.append(metadata)
        return emails

    async def _fetch_metadata(
        self,
        message_id: str,
        token: str,
        client: httpx.AsyncClient,
    ) -> Optional[dict]:
        """Fetch metadata (From, Subject, snippet) for a single message."""
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

    async def setup_watch(self, topic_name: str) -> Optional[str]:
        """Set up Gmail push notifications to a Pub/Sub topic.

        Args:
            topic_name: Full Pub/Sub topic name
                        (e.g., "projects/PROJECT/topics/email-notifications").

        Returns:
            historyId string on success, or None on failure.
        """
        token = await self._get_token()
        if not token:
            logger.error("No token for setup_watch")
            return None

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{GMAIL_API_BASE}/watch",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "labelIds": ["INBOX"],
                        "topicName": topic_name,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                history_id = data.get("historyId")
                logger.info(f"Gmail watch set up, historyId={history_id}")
                return history_id
            except Exception as e:
                logger.error(f"watch setup failed: {e}")
                return None

    async def renew_watch(self, topic_name: str) -> Optional[str]:
        """Renew Gmail watch (same as setup_watch, Gmail requires periodic renewal)."""
        return await self.setup_watch(topic_name)


# ──────────────────────────── helpers ────────────────────────────


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""
