"""Message channel definitions for SMS and WhatsApp support."""
from enum import Enum


class MessageChannel(str, Enum):
    """Messaging channel for incoming/outgoing messages."""

    SMS = "sms"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"

    @classmethod
    def from_twilio_address(cls, address: str) -> tuple["MessageChannel", str]:
        """Parse Twilio address to extract channel and phone number.

        Args:
            address: Twilio From/To address (e.g., "whatsapp:+14155551234" or "+14155551234")

        Returns:
            Tuple of (channel, phone_number in E.164 format)
        """
        if address.startswith("whatsapp:"):
            return cls.WHATSAPP, address[9:]
        return cls.SMS, address

    def format_address(self, phone_number: str) -> str:
        """Format phone number for Twilio API based on channel.

        Args:
            phone_number: Phone number in E.164 format (e.g., "+14155551234")

        Returns:
            Formatted address for Twilio (e.g., "whatsapp:+14155551234" or "+14155551234")
        """
        if self == MessageChannel.WHATSAPP:
            return f"whatsapp:{phone_number}"
        return phone_number
