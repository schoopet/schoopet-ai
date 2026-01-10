"""Twilio SMS and WhatsApp sending functionality."""
import asyncio
import logging
from typing import Optional

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from ..channel import MessageChannel

logger = logging.getLogger(__name__)


class SMSSender:
    """Sends SMS and WhatsApp messages via Twilio Messaging API."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        whatsapp_from_number: Optional[str] = None,
    ):
        """Initialize SMS sender with Twilio credentials.

        Args:
            account_sid: Twilio Account SID.
            auth_token: Twilio Auth Token.
            from_number: Twilio phone number to send SMS from (E.164 format).
            whatsapp_from_number: Twilio phone number for WhatsApp (defaults to from_number).
        """
        self._client = TwilioClient(account_sid, auth_token)
        self._from_number = from_number
        self._whatsapp_from_number = whatsapp_from_number or from_number

    async def send(
        self,
        to_number: str,
        body: str,
        channel: MessageChannel = MessageChannel.SMS,
    ) -> str:
        """Send a single SMS or WhatsApp message.

        Twilio automatically handles message splitting for SMS (concatenated SMS)
        and WhatsApp has a high character limit (~1600 chars).

        Args:
            to_number: Recipient phone number in E.164 format.
            body: Message body text.
            channel: Message channel (SMS or WhatsApp).

        Returns:
            The Twilio MessageSid on success.

        Raises:
            TwilioRestException: If sending fails.
        """
        # Format addresses based on channel
        if channel == MessageChannel.WHATSAPP:
            from_addr = f"whatsapp:{self._whatsapp_from_number}"
            to_addr = f"whatsapp:{to_number}"
        else:
            from_addr = self._from_number
            to_addr = to_number

        # Run synchronous Twilio client in thread pool
        message = await asyncio.to_thread(
            self._client.messages.create,
            to=to_addr,
            from_=from_addr,
            body=body,
        )

        logger.info(
            f"Sent {channel.value} to {to_number}: MessageSid={message.sid}, "
            f"Status={message.status}"
        )

        return message.sid
