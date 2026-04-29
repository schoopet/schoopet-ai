"""Unit tests for the Agent Engine client wrapper."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.adk.events import Event
from google.genai import types

from src.agent.client import AgentEngineClient, AdkConfirmationRequest


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


@pytest.mark.asyncio
async def test_send_message_events_validates_dict_events():
    event_dict = {
        "author": "agent",
        "content": {"role": "model", "parts": [{"text": "Hello"}]},
    }
    client = AgentEngineClient.__new__(AgentEngineClient)
    client._timeout = 1
    client._adk_app = _FakeAdkApp([event_dict])
    client._init_client = MagicMock()

    events = await client.send_message_events(
        user_id="user-123",
        session_id="session-123",
        message="hi",
    )

    assert len(events) == 1
    assert isinstance(events[0], Event)
    assert AgentEngineClient.extract_text(events) == "Hello"


def test_extract_confirmation_requests_finds_native_function_calls():
    event = Event(
        author="agent",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="adk_request_confirmation",
                        id="confirm-1",
                        args={
                            "originalFunctionCall": {
                                "id": "tool-1",
                                "name": "send_email",
                                "args": {"to": "person@example.com"},
                            },
                            "toolConfirmation": {
                                "hint": "Send this email?",
                                "payload": {"risk": "external_send"},
                            },
                        },
                    )
                )
            ],
        ),
    )

    confirmations = AgentEngineClient.extract_confirmation_requests([event])

    assert len(confirmations) == 1
    assert confirmations[0].function_call_id == "confirm-1"
    assert confirmations[0].tool_name == "send_email"
    assert confirmations[0].tool_args == {"to": "person@example.com"}
    assert confirmations[0].original_function_call_id == "tool-1"
    assert confirmations[0].hint == "Send this email?"


@pytest.mark.asyncio
async def test_send_confirmation_response_sends_native_function_response():
    client = AgentEngineClient.__new__(AgentEngineClient)
    client.send_message_events = AsyncMock(return_value=[])

    await client.send_confirmation_response(
        user_id="user-123",
        session_id="session-123",
        confirmation_function_call_id="confirm-1",
        confirmed=False,
    )

    message = client.send_message_events.call_args.args[2]
    function_response = message.parts[0].function_response
    assert function_response.name == "adk_request_confirmation"
    assert function_response.id == "confirm-1"
    assert function_response.response == {"confirmed": False}


def test_confirmation_request_serializes_for_firestore():
    confirmation = AdkConfirmationRequest(
        function_call_id="confirm-1",
        original_function_call={"id": "tool-1", "name": "send_email", "args": {"to": "x"}},
        tool_confirmation={"hint": "approve?", "payload": {"kind": "email"}},
    )

    data = confirmation.to_firestore()

    assert data["function_call_id"] == "confirm-1"
    assert data["tool_name"] == "send_email"
    assert data["tool_args"] == {"to": "x"}
