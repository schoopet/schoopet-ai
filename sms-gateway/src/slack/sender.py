"""Slack Bot API message sending functionality."""
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Conservative Slack message length limit (actual limit is higher, but keep safe)
MAX_MESSAGE_LENGTH = 3000

SLACK_API_BASE = "https://slack.com/api"


class SlackSender:
    """Sends messages via Slack Web API."""

    def __init__(self, bot_token: str):
        """Initialize Slack sender with bot token.

        Args:
            bot_token: Slack Bot User OAuth Token (xoxb-...).
        """
        self._headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(timeout=30.0)
        # Cache user_id -> DM channel_id to avoid repeated conversations.open calls
        self._channel_cache: Dict[str, str] = {}

    async def send(self, user_id: str, text: str) -> List[str]:
        """Send a DM to a Slack user.

        Opens (or reuses) a DM channel with the user, then sends the message,
        splitting if necessary.

        Args:
            user_id: Slack user ID (e.g., "U0123456789").
            text: Message text to send.

        Returns:
            List of Slack message timestamps on success.
        """
        if not text:
            text = "(empty response)"

        channel_id = await self._get_dm_channel(user_id)
        chunks = _split_message(text)
        message_timestamps = []

        for i, chunk in enumerate(chunks):
            response = await self._client.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                headers=self._headers,
                json={"channel": channel_id, "text": chunk},
            )
            response.raise_for_status()
            result = response.json()

            if result.get("ok"):
                ts = result["message"]["ts"]
                message_timestamps.append(ts)
                logger.info(
                    f"Sent Slack part {i+1}/{len(chunks)} to user {user_id} "
                    f"(channel {channel_id}): ts={ts}"
                )
            else:
                logger.error(
                    f"Slack API error sending to {user_id}: {result.get('error')}"
                )

        return message_timestamps

    async def _get_dm_channel(self, user_id: str) -> str:
        """Get or open a DM channel with a user.

        Uses an in-memory cache to avoid calling conversations.open repeatedly.

        Args:
            user_id: Slack user ID.

        Returns:
            DM channel ID string.
        """
        if user_id in self._channel_cache:
            return self._channel_cache[user_id]

        response = await self._client.post(
            f"{SLACK_API_BASE}/conversations.open",
            headers=self._headers,
            json={"users": user_id},
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Failed to open DM with {user_id}: {result.get('error')}"
            )

        channel_id = result["channel"]["id"]
        self._channel_cache[user_id] = channel_id
        logger.info(f"Opened DM channel {channel_id} for Slack user {user_id}")
        return channel_id

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()


def _split_message(text: str) -> List[str]:
    """Split a message into chunks that fit within Slack's limit.

    Tries to split at newlines first, then at spaces, then hard-cuts.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= MAX_MESSAGE_LENGTH:
            chunks.append(remaining)
            break

        # Try to find a newline to split at
        split_at = remaining.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            # Try space
            split_at = remaining.rfind(" ", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            # Hard cut
            split_at = MAX_MESSAGE_LENGTH

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return chunks
