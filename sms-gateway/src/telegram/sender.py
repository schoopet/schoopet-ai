"""Telegram Bot API message sending functionality."""
import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)

# Telegram Bot API message length limit
MAX_MESSAGE_LENGTH = 4096


class TelegramSender:
    """Sends messages via Telegram Bot API."""

    def __init__(self, bot_token: str):
        """Initialize Telegram sender with bot token.

        Args:
            bot_token: Telegram Bot API token from @BotFather.
        """
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send(self, chat_id: str, text: str) -> List[int]:
        """Send a message to a Telegram chat.

        Handles message splitting if the text exceeds Telegram's 4096 character limit.

        Args:
            chat_id: Telegram chat ID (numeric string).
            text: Message text.

        Returns:
            List of Telegram message IDs on success.
        """
        if not text:
            text = "(empty response)"

        chunks = _split_message(text)
        message_ids = []

        for i, chunk in enumerate(chunks):
            response = await self._client.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
            )
            response.raise_for_status()
            result = response.json()

            if result.get("ok"):
                msg_id = result["result"]["message_id"]
                message_ids.append(msg_id)
                logger.info(
                    f"Sent Telegram part {i+1}/{len(chunks)} to {chat_id}: "
                    f"message_id={msg_id}"
                )
            else:
                logger.error(
                    f"Telegram API error for chat {chat_id}: {result}"
                )

        return message_ids

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()


def _split_message(text: str) -> List[str]:
    """Split a message into chunks that fit within Telegram's limit.

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
