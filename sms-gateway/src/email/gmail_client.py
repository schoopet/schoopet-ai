"""Gmail API client for the email processing module.

Uses the system gmail_system OAuth token (stored under phone key "email_system",
feature "gmail_system") to read the dedicated inbox.
"""
import base64
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Fixed identifiers for the system Gmail account
SYSTEM_PHONE = "email_system"
SYSTEM_FEATURE = "gmail_system"

# MIME types natively understood by Gemini as inline_data
GEMINI_SUPPORTED_MIME_TYPES = {
    # Documents
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/tiff",
    # Audio
    "audio/wav",
    "audio/mp3",
    "audio/mpeg",
    "audio/aiff",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
    # Video
    "video/mp4",
    "video/mpeg",
    "video/mov",
    "video/avi",
    "video/x-flv",
    "video/mpg",
    "video/webm",
    "video/wmv",
    "video/3gpp",
}


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

    async def get_new_messages(self, history_id: str) -> list[dict]:
        """Fetch new messages using Gmail history.list since history_id.

        Returns a list of parsed email dicts:
            {id, thread_id, from, to, subject, date, body, snippet}
        """
        token = await self._get_token()
        if not token:
            logger.error("No gmail_system token available — cannot fetch messages")
            return []

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{GMAIL_API_BASE}/history",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "startHistoryId": history_id,
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
                parsed = await self.fetch_message(msg_id, token=token, client=client)
                if parsed:
                    emails.append(parsed)
            return emails

    async def _get_recent_messages(
        self, token: str, client: httpx.AsyncClient, max_results: int = 10
    ) -> list[dict]:
        """Fallback: list recent messages from INBOX."""
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
            parsed = await self.fetch_message(msg["id"], token=token, client=client)
            if parsed:
                emails.append(parsed)
        return emails

    async def fetch_message(
        self,
        message_id: str,
        token: str = None,
        client: httpx.AsyncClient = None,
    ) -> Optional[dict]:
        """Fetch and parse a single Gmail message.

        Returns a dict with keys: id, thread_id, from, to, subject, date, body,
        snippet, attachments. Each attachment is a dict with filename, mime_type,
        and bytes (bytes or None for unsupported types).
        """
        own_client = client is None
        if token is None:
            token = await self._get_token()
            if not token:
                return None

        try:
            async with (httpx.AsyncClient() if own_client else _noop_ctx(client)) as c:
                c = client if client else c
                resp = await c.get(
                    f"{GMAIL_API_BASE}/messages/{message_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"format": "full"},
                )
                resp.raise_for_status()
                msg = resp.json()
                payload = msg.get("payload", {})
                attachments = await _collect_attachments(
                    payload, c, token, message_id
                )
        except Exception as e:
            logger.error(f"messages.get({message_id}) failed: {e}")
            return None

        result = _parse_message(msg)
        result["attachments"] = attachments
        return result

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


class _noop_ctx:
    """Context manager that wraps an already-open httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *args):
        pass


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(part: dict) -> str:
    """Decode base64url-encoded message body."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_text(payload: dict) -> str:
    """Recursively extract text/plain body from MIME payload."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        return _decode_body(payload)

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_text(part)
            if text:
                return text

    return ""


async def _collect_attachments(
    payload: dict,
    client: httpx.AsyncClient,
    token: str,
    message_id: str,
) -> list[dict]:
    """Recursively walk the MIME tree and collect non-text-body attachment data.

    For each attachment part:
    - If the MIME type is in GEMINI_SUPPORTED_MIME_TYPES: fetch bytes (inline or via
      the Gmail attachments API) and return {"filename", "mime_type", "bytes": bytes}.
    - Otherwise: return {"filename", "mime_type", "bytes": None} so the caller can
      emit a text note about the unsupported attachment.

    text/plain parts are skipped (they are handled as the email body by _extract_text).
    """
    results: list[dict] = []
    await _walk_parts(payload, client, token, message_id, results)
    return results


async def _walk_parts(
    part: dict,
    client: httpx.AsyncClient,
    token: str,
    message_id: str,
    results: list[dict],
) -> None:
    mime_type = part.get("mimeType", "")

    # Skip the plain-text body — _extract_text already handles it
    if mime_type == "text/plain":
        return

    # Recurse into multipart containers
    if mime_type.startswith("multipart/"):
        for sub in part.get("parts", []):
            await _walk_parts(sub, client, token, message_id, results)
        return

    # Skip text/html and other inline text variants without a filename
    filename = part.get("filename", "")
    body = part.get("body", {})
    if not filename and not body.get("attachmentId") and not body.get("data"):
        return

    # Determine bytes for supported types; None for unsupported
    attachment_bytes: Optional[bytes] = None
    if mime_type in GEMINI_SUPPORTED_MIME_TYPES:
        attachment_bytes = await _fetch_attachment_bytes(
            part, client, token, message_id
        )

    results.append(
        {
            "filename": filename or "(unnamed)",
            "mime_type": mime_type,
            "bytes": attachment_bytes,
        }
    )


async def _fetch_attachment_bytes(
    part: dict,
    client: httpx.AsyncClient,
    token: str,
    message_id: str,
) -> Optional[bytes]:
    """Decode or download attachment bytes for a single MIME part."""
    body = part.get("body", {})

    # Small/inline attachment: data is embedded in the payload
    if body.get("data"):
        try:
            return base64.urlsafe_b64decode(body["data"] + "==")
        except Exception as e:
            logger.warning(f"Failed to decode inline attachment data: {e}")
            return None

    # Large attachment: fetch via attachments.get
    attachment_id = body.get("attachmentId")
    if not attachment_id:
        return None

    try:
        resp = await client.get(
            f"{GMAIL_API_BASE}/messages/{message_id}/attachments/{attachment_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==")
    except Exception as e:
        logger.warning(f"Failed to fetch attachment {attachment_id}: {e}")

    return None


def _parse_message(msg: dict) -> dict:
    """Convert raw Gmail API message to a simple dict."""
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "date": _get_header(headers, "Date"),
        "body": _extract_text(payload),
        "snippet": msg.get("snippet", ""),
    }
