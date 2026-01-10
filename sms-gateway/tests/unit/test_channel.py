"""Unit tests for MessageChannel enum and WhatsApp support."""
import pytest

from src.channel import MessageChannel


class TestMessageChannel:
    """Tests for MessageChannel enum."""

    def test_from_twilio_address_sms(self):
        """Should parse regular SMS phone number."""
        channel, phone = MessageChannel.from_twilio_address("+14155551234")

        assert channel == MessageChannel.SMS
        assert phone == "+14155551234"

    def test_from_twilio_address_whatsapp(self):
        """Should parse WhatsApp address and extract phone number."""
        channel, phone = MessageChannel.from_twilio_address("whatsapp:+14155551234")

        assert channel == MessageChannel.WHATSAPP
        assert phone == "+14155551234"

    def test_from_twilio_address_whatsapp_preserves_format(self):
        """Should preserve E.164 format when extracting from WhatsApp."""
        channel, phone = MessageChannel.from_twilio_address("whatsapp:+19494136310")

        assert channel == MessageChannel.WHATSAPP
        assert phone == "+19494136310"
        assert phone.startswith("+")

    def test_format_address_sms(self):
        """Should return phone number unchanged for SMS."""
        address = MessageChannel.SMS.format_address("+14155551234")

        assert address == "+14155551234"

    def test_format_address_whatsapp(self):
        """Should add whatsapp: prefix for WhatsApp."""
        address = MessageChannel.WHATSAPP.format_address("+14155551234")

        assert address == "whatsapp:+14155551234"

    def test_channel_is_string_enum(self):
        """MessageChannel values should be usable as strings."""
        assert MessageChannel.SMS.value == "sms"
        assert MessageChannel.WHATSAPP.value == "whatsapp"
        assert str(MessageChannel.SMS) == "MessageChannel.SMS"

    def test_roundtrip_sms(self):
        """SMS address should roundtrip through parse and format."""
        original = "+14155551234"
        channel, phone = MessageChannel.from_twilio_address(original)
        formatted = channel.format_address(phone)

        assert formatted == original

    def test_roundtrip_whatsapp(self):
        """WhatsApp address should roundtrip through parse and format."""
        original = "whatsapp:+14155551234"
        channel, phone = MessageChannel.from_twilio_address(original)
        formatted = channel.format_address(phone)

        assert formatted == original


class TestWhatsAppIntegration:
    """Integration tests for WhatsApp message handling."""

    @pytest.mark.asyncio
    async def test_whatsapp_message_processing(self):
        """WhatsApp message should be processed with correct channel."""
        from unittest.mock import AsyncMock, MagicMock
        from src.webhook.handler import process_message_async, init_services

        # Setup mocks
        session_manager = AsyncMock()
        user_info = MagicMock()
        user_info.opted_in = True
        user_info.is_new_user = False
        session_manager.get_or_create_user.return_value = user_info

        session_info = MagicMock()
        session_info.agent_session_id = "session-123"
        session_info.is_new_session = False
        session_manager.get_or_create_session.return_value = session_info

        agent_client = AsyncMock()
        agent_client.send_message.return_value = "Hello from agent!"

        sms_sender = AsyncMock()
        sms_sender.send.return_value = "SM123"

        rate_limiter = AsyncMock()
        rate_limiter.check_and_increment.return_value = (True, 1)

        init_services(
            validator=MagicMock(),
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            rate_limiter=rate_limiter,
        )

        # Process WhatsApp message
        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.WHATSAPP,
        )

        # Verify send was called with WhatsApp channel
        sms_sender.send.assert_called()
        call_kwargs = sms_sender.send.call_args
        assert call_kwargs.kwargs.get("channel") == MessageChannel.WHATSAPP

    @pytest.mark.asyncio
    async def test_sms_message_processing(self):
        """SMS message should be processed with SMS channel."""
        from unittest.mock import AsyncMock, MagicMock
        from src.webhook.handler import process_message_async, init_services

        # Setup mocks
        session_manager = AsyncMock()
        user_info = MagicMock()
        user_info.opted_in = True
        user_info.is_new_user = False
        session_manager.get_or_create_user.return_value = user_info

        session_info = MagicMock()
        session_info.agent_session_id = "session-123"
        session_info.is_new_session = False
        session_manager.get_or_create_session.return_value = session_info

        agent_client = AsyncMock()
        agent_client.send_message.return_value = "Hello from agent!"

        sms_sender = AsyncMock()
        sms_sender.send.return_value = "SM123"

        rate_limiter = AsyncMock()
        rate_limiter.check_and_increment.return_value = (True, 1)

        init_services(
            validator=MagicMock(),
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            rate_limiter=rate_limiter,
        )

        # Process SMS message
        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.SMS,
        )

        # Verify send was called with SMS channel
        sms_sender.send.assert_called()
        call_kwargs = sms_sender.send.call_args
        assert call_kwargs.kwargs.get("channel") == MessageChannel.SMS


class TestSMSSenderChannel:
    """Tests for SMSSender channel handling."""

    def test_sender_formats_whatsapp_addresses(self):
        """SMSSender should format WhatsApp addresses correctly."""
        from unittest.mock import MagicMock, patch
        from src.sms.sender import SMSSender

        with patch("src.sms.sender.TwilioClient"):
            sender = SMSSender(
                account_sid="AC123",
                auth_token="token",
                from_number="+14155559999",
                whatsapp_from_number="+14155238886",
            )

        # Verify the WhatsApp from number is stored
        assert sender._whatsapp_from_number == "+14155238886"
        assert sender._from_number == "+14155559999"

    def test_sender_defaults_whatsapp_to_sms_number(self):
        """SMSSender should default WhatsApp number to SMS number."""
        from unittest.mock import patch
        from src.sms.sender import SMSSender

        with patch("src.sms.sender.TwilioClient"):
            sender = SMSSender(
                account_sid="AC123",
                auth_token="token",
                from_number="+14155559999",
            )

        assert sender._whatsapp_from_number == "+14155559999"
        assert sender._from_number == "+14155559999"
