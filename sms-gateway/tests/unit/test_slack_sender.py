"""Unit tests for SlackSender."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.slack.sender import SlackSender, _split_message, MAX_MESSAGE_LENGTH


class TestSlackSender(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sender = SlackSender(bot_token="xoxb-test-token")
        # Replace the httpx client with a mock
        self.mock_client = AsyncMock()
        self.sender._client = self.mock_client

    def _make_response(self, ok: bool, payload: dict):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": ok, **payload}
        return mock_resp

    async def _setup_open_channel(self, channel_id: str = "D0123456"):
        self.mock_client.post.return_value = self._make_response(
            True, {"channel": {"id": channel_id}}
        )

    async def test_send_opens_dm_channel_and_posts_message(self):
        open_resp = self._make_response(True, {"channel": {"id": "D0123456"}})
        post_resp = self._make_response(True, {"message": {"ts": "1234567890.000001"}})
        self.mock_client.post.side_effect = [open_resp, post_resp]

        result = await self.sender.send("U0123456", "Hello Slack!")

        self.assertEqual(result, ["1234567890.000001"])
        self.assertEqual(self.mock_client.post.call_count, 2)

        # First call: conversations.open
        first_call = self.mock_client.post.call_args_list[0]
        self.assertIn("conversations.open", first_call.args[0])
        self.assertEqual(first_call.kwargs["json"]["users"], "U0123456")

        # Second call: chat.postMessage
        second_call = self.mock_client.post.call_args_list[1]
        self.assertIn("chat.postMessage", second_call.args[0])
        self.assertEqual(second_call.kwargs["json"]["text"], "Hello Slack!")
        self.assertEqual(second_call.kwargs["json"]["channel"], "D0123456")

    async def test_send_caches_dm_channel(self):
        open_resp = self._make_response(True, {"channel": {"id": "D0123456"}})
        post_resp1 = self._make_response(True, {"message": {"ts": "111.000"}})
        post_resp2 = self._make_response(True, {"message": {"ts": "222.000"}})
        self.mock_client.post.side_effect = [open_resp, post_resp1, post_resp2]

        await self.sender.send("U0123456", "First message")
        await self.sender.send("U0123456", "Second message")

        # conversations.open called only once; chat.postMessage called twice
        calls = [c.args[0] for c in self.mock_client.post.call_args_list]
        open_calls = [c for c in calls if "conversations.open" in c]
        post_calls = [c for c in calls if "chat.postMessage" in c]
        self.assertEqual(len(open_calls), 1)
        self.assertEqual(len(post_calls), 2)

    async def test_send_long_message_splits(self):
        open_resp = self._make_response(True, {"channel": {"id": "D0123456"}})
        post_resp1 = self._make_response(True, {"message": {"ts": "111.000"}})
        post_resp2 = self._make_response(True, {"message": {"ts": "222.000"}})
        self.mock_client.post.side_effect = [open_resp, post_resp1, post_resp2]

        long_text = "a" * (MAX_MESSAGE_LENGTH + 100)
        result = await self.sender.send("U0123456", long_text)

        self.assertEqual(result, ["111.000", "222.000"])
        post_calls = [
            c for c in self.mock_client.post.call_args_list
            if "chat.postMessage" in c.args[0]
        ]
        self.assertEqual(len(post_calls), 2)

    async def test_send_empty_text_sends_placeholder(self):
        open_resp = self._make_response(True, {"channel": {"id": "D0123456"}})
        post_resp = self._make_response(True, {"message": {"ts": "123.000"}})
        self.mock_client.post.side_effect = [open_resp, post_resp]

        await self.sender.send("U0123456", "")

        post_call = [
            c for c in self.mock_client.post.call_args_list
            if "chat.postMessage" in c.args[0]
        ][0]
        self.assertEqual(post_call.kwargs["json"]["text"], "(empty response)")

    async def test_conversations_open_failure_raises(self):
        fail_resp = self._make_response(False, {"error": "not_in_channel"})
        self.mock_client.post.return_value = fail_resp

        with self.assertRaises(RuntimeError):
            await self.sender.send("U0123456", "Hello")


class TestSplitMessage:
    def test_short_message_not_split(self):
        text = "Hello world"
        assert _split_message(text) == [text]

    def test_long_message_split_at_newline(self):
        part1 = "a" * (MAX_MESSAGE_LENGTH - 10) + "\n"
        part2 = "b" * 50
        text = part1 + part2
        chunks = _split_message(text)
        assert len(chunks) == 2
        assert "b" * 50 in chunks[1]

    def test_long_message_hard_cut_when_no_space(self):
        text = "a" * (MAX_MESSAGE_LENGTH + 100)
        chunks = _split_message(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == MAX_MESSAGE_LENGTH

    def test_exactly_at_limit_not_split(self):
        text = "x" * MAX_MESSAGE_LENGTH
        chunks = _split_message(text)
        assert len(chunks) == 1
