"""Discord message sending via REST API."""
import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)

# Discord's message content length limit
MAX_MESSAGE_LENGTH = 2000

DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordSender:
    """Sends messages via the Discord REST API.

    Supports two delivery modes:
    - followup(): Edit a deferred interaction response (used by the handler).
    - send(): Open a DM and deliver a message (used by the internal notifier).
    """

    def __init__(self, application_id: str, bot_token: str):
        """Initialize Discord sender.

        Args:
            application_id: Discord application (client) ID.
            bot_token: Discord bot token (from Developer Portal → Bot).
        """
        self._application_id = application_id
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bot {bot_token}"},
        )

    async def followup(self, interaction_token: str, text: str) -> None:
        """Edit the original deferred interaction response with the agent reply.

        Args:
            interaction_token: The interaction token from the Discord webhook payload.
            text: Message text to send.
        """
        if not text:
            text = "(empty response)"

        chunks = _split_message(text)
        url = (
            f"{DISCORD_API_BASE}/webhooks/{self._application_id}"
            f"/{interaction_token}/messages/@original"
        )

        # Patch the first chunk into the original deferred message
        response = await self._client.patch(url, json={"content": chunks[0]})
        response.raise_for_status()
        logger.info(f"Sent Discord interaction followup part 1/{len(chunks)}")

        # Send remaining chunks as new followup messages
        followup_url = (
            f"{DISCORD_API_BASE}/webhooks/{self._application_id}/{interaction_token}"
        )
        for i, chunk in enumerate(chunks[1:], start=2):
            response = await self._client.post(followup_url, json={"content": chunk})
            response.raise_for_status()
            logger.info(f"Sent Discord interaction followup part {i}/{len(chunks)}")

    async def send(self, discord_user_id: str, text: str) -> None:
        """Send a direct message to a Discord user.

        Used by the internal handler for async task notifications when the
        original interaction context is no longer available.

        Args:
            discord_user_id: Discord user snowflake ID.
            text: Message text to send.
        """
        if not text:
            text = "(empty response)"

        logger.info(f"Discord DM open: opening channel for user {discord_user_id}")
        dm_response = await self._client.post(
            f"{DISCORD_API_BASE}/users/@me/channels",
            json={"recipient_id": discord_user_id},
        )
        if dm_response.status_code >= 400:
            logger.error(
                f"Discord DM open failed for user {discord_user_id}: "
                f"status={dm_response.status_code}, body={dm_response.text!r}"
            )
            dm_response.raise_for_status()
        channel_id = dm_response.json()["id"]
        logger.info(f"Discord DM open ok: channel_id={channel_id} for user {discord_user_id}")

        chunks = _split_message(text)
        for i, chunk in enumerate(chunks, start=1):
            response = await self._client.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                json={"content": chunk},
            )
            if response.status_code >= 400:
                logger.error(
                    f"Discord DM post failed for user {discord_user_id} "
                    f"channel {channel_id} part {i}/{len(chunks)}: "
                    f"status={response.status_code}, body={response.text!r}"
                )
                response.raise_for_status()
            logger.info(
                f"Sent Discord DM part {i}/{len(chunks)} to user {discord_user_id}"
            )

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()


def _split_message(text: str) -> List[str]:
    """Split text into chunks within Discord's 2000-character limit.

    Tries to split at newlines, then spaces, then hard-cuts.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= MAX_MESSAGE_LENGTH:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = MAX_MESSAGE_LENGTH

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n ")

    return chunks
