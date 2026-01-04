"""Twilio SMS sending functionality."""
import asyncio
import logging
from typing import List

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)


class SMSSender:
    """Sends SMS messages via Twilio Messaging API."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        segment_delay_ms: int = 500,
    ):
        """Initialize SMS sender with Twilio credentials.

        Args:
            account_sid: Twilio Account SID.
            auth_token: Twilio Auth Token.
            from_number: Twilio phone number to send from (E.164 format).
            segment_delay_ms: Delay between multi-part messages in milliseconds.
        """
        self._client = TwilioClient(account_sid, auth_token)
        self._from_number = from_number
        self._segment_delay = segment_delay_ms / 1000  # Convert to seconds

    async def send(self, to_number: str, body: str) -> str:
        """Send a single SMS message.

        Args:
            to_number: Recipient phone number in E.164 format.
            body: Message body text.

        Returns:
            The Twilio MessageSid on success.

        Raises:
            TwilioRestException: If sending fails.
        """
        # Run synchronous Twilio client in thread pool
        message = await asyncio.to_thread(
            self._client.messages.create,
            to=to_number,
            from_=self._from_number,
            body=body,
        )

        logger.info(
            f"Sent SMS to {to_number}: MessageSid={message.sid}, "
            f"Status={message.status}"
        )

        return message.sid

    async def send_multi(
        self,
        to_number: str,
        segments: List[str],
        max_retries: int = 2,
    ) -> List[str]:
        """Send multiple SMS segments with delay between each.

        The delay helps ensure messages arrive in order, as SMS delivery
        order is not guaranteed.

        Args:
            to_number: Recipient phone number in E.164 format.
            segments: List of message segments to send.
            max_retries: Number of retries per segment on failure.

        Returns:
            List of MessageSids for successfully sent messages.
        """
        message_sids = []

        for i, segment in enumerate(segments):
            # Retry logic for individual segments
            for attempt in range(max_retries + 1):
                try:
                    sid = await self.send(to_number, segment)
                    message_sids.append(sid)
                    break
                except TwilioRestException as e:
                    if attempt < max_retries:
                        logger.warning(
                            f"Failed to send segment {i + 1}/{len(segments)}, "
                            f"attempt {attempt + 1}/{max_retries + 1}: {e}"
                        )
                        await asyncio.sleep(1)  # Wait before retry
                    else:
                        logger.error(
                            f"Failed to send segment {i + 1}/{len(segments)} "
                            f"after {max_retries + 1} attempts: {e}"
                        )
                        raise

            # Delay before sending next segment (except for last one)
            if i < len(segments) - 1:
                await asyncio.sleep(self._segment_delay)

        logger.info(
            f"Sent {len(message_sids)} SMS segments to {to_number}"
        )

        return message_sids
