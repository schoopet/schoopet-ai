"""Twilio SMS and WhatsApp sending functionality."""
import asyncio
import logging
import textwrap
from typing import List, Optional

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from ..channel import MessageChannel

logger = logging.getLogger(__name__)

# Twilio API limit for body length
MAX_BODY_LENGTH = 1600


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
    ) -> List[str]:
        """Send a single SMS or WhatsApp message.

        Handles message splitting if the body exceeds Twilio's 1600 character limit.

        Args:
            to_number: Recipient phone number in E.164 format.
            body: Message body text.
            channel: Message channel (SMS or WhatsApp).

        Returns:
            List of Twilio MessageSids on success.

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

        # Split message if it exceeds the limit
        # replace_whitespace=False preserves newlines which is important for formatting
        chunks = textwrap.wrap(
            body,
            width=MAX_BODY_LENGTH,
            replace_whitespace=False,
            drop_whitespace=False
        )
        
        if not chunks:
            # Handle empty body case if it ever happens
            chunks = [""]

        message_sids = []

        for i, chunk in enumerate(chunks):
            # Run synchronous Twilio client in thread pool
            message = await asyncio.to_thread(
                self._client.messages.create,
                to=to_addr,
                from_=from_addr,
                body=chunk,
            )
            
            message_sids.append(message.sid)

            logger.info(
                f"Sent {channel.value} part {i+1}/{len(chunks)} to {to_number}: "
                f"MessageSid={message.sid}, Status={message.status}"
            )

        return message_sids
