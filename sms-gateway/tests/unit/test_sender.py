import unittest
from unittest.mock import MagicMock, patch
import asyncio
from src.sms.sender import SMSSender, MAX_BODY_LENGTH
from src.channel import MessageChannel

class TestSMSSender(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.sender = SMSSender("sid", "token", "+1234567890")
        # Replace the real client with our mock
        self.sender._client = self.mock_client

    async def test_send_short_message(self):
        message_sid = "SM123"
        mock_message = MagicMock()
        mock_message.sid = message_sid
        mock_message.status = "queued"
        self.mock_client.messages.create.return_value = mock_message

        result = await self.sender.send("+1987654321", "Hello world")

        self.assertEqual(result, [message_sid])
        self.mock_client.messages.create.assert_called_once_with(
            to="+1987654321",
            from_="+1234567890",
            body="Hello world"
        )

    async def test_send_long_message(self):
        # Create a message longer than MAX_BODY_LENGTH (1600)
        # 1600 chars + 10 chars
        long_message = "a" * (MAX_BODY_LENGTH + 10)
        
        mock_message1 = MagicMock()
        mock_message1.sid = "SM1"
        mock_message1.status = "queued"
        
        mock_message2 = MagicMock()
        mock_message2.sid = "SM2"
        mock_message2.status = "queued"
        
        self.mock_client.messages.create.side_effect = [mock_message1, mock_message2]

        result = await self.sender.send("+1987654321", long_message)

        self.assertEqual(result, ["SM1", "SM2"])
        self.assertEqual(self.mock_client.messages.create.call_count, 2)
        
        # Verify first chunk
        call_args_list = self.mock_client.messages.create.call_args_list
        self.assertEqual(len(call_args_list[0].kwargs['body']), MAX_BODY_LENGTH)
        
        # Verify second chunk
        self.assertEqual(len(call_args_list[1].kwargs['body']), 10)

    async def test_send_whatsapp(self):
        message_sid = "WA123"
        mock_message = MagicMock()
        mock_message.sid = message_sid
        mock_message.status = "queued"
        self.mock_client.messages.create.return_value = mock_message

        result = await self.sender.send("+1987654321", "Hello WhatsApp", channel=MessageChannel.WHATSAPP)

        self.assertEqual(result, [message_sid])
        self.mock_client.messages.create.assert_called_once_with(
            to="whatsapp:+1987654321",
            from_="whatsapp:+1234567890",
            body="Hello WhatsApp"
        )
