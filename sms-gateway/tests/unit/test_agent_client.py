"""Unit tests for the Agent Engine client wrapper."""
from unittest.mock import MagicMock

import pytest

from src.agent.client import AgentEngineClient


class _FakeAdkApp:
    def __init__(self, events):
        self._events = events

    async def async_stream_query(self, **kwargs):
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_send_message_collects_text_from_dict_events():
    client = AgentEngineClient.__new__(AgentEngineClient)
    client._timeout = 1
    client._adk_app = _FakeAdkApp(
        [
            {"content": {"parts": [{"text": "Hello "}, {"text": "world"}]}},
            {"content": {"parts": [{"text": "!"}]}},
        ]
    )
    client._init_client = MagicMock()

    response = await client.send_message(
        user_id="user-123",
        session_id="session-123",
        message="hi",
    )

    assert response == "Hello world!"
    client._init_client.assert_not_called()
