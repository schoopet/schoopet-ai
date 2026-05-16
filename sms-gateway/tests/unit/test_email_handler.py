from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.adk.events import Event
from google.genai import types

from src.agent.client import AgentEngineClient
from src.email import handler


def _text_events(text: str) -> list[Event]:
    return [
        Event(
            author="agent",
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        )
    ]


def _confirmation_events() -> list[Event]:
    return [
        Event(
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
                                    "args": {"to": "x@example.com"},
                                },
                                "toolConfirmation": {"hint": "Send email?"},
                            },
                        )
                    )
                ],
            ),
        )
    ]


def test_should_suppress_response_exact_prefix():
    assert handler._should_suppress_response("<SUPPRESS RESPONSE>")


def test_should_suppress_response_with_leading_whitespace():
    assert handler._should_suppress_response("\n  \t<SUPPRESS RESPONSE>\nprocessed")


def test_should_suppress_response_marker_later_does_not_suppress():
    assert not handler._should_suppress_response("Summary\n<SUPPRESS RESPONSE>")


def test_should_suppress_response_normal_message_does_not_suppress():
    assert not handler._should_suppress_response("Here is the summary.")


@pytest.fixture
def email_services(monkeypatch):
    agent_client = AsyncMock()
    session_manager = AsyncMock()
    session_manager.get_or_create_session = AsyncMock(
        return_value=SimpleNamespace(agent_session_id="agent-session-123")
    )
    discord_sender = AsyncMock()
    agent_client.extract_text = AgentEngineClient.extract_text
    agent_client.extract_confirmation_requests = AgentEngineClient.extract_confirmation_requests
    agent_client.extract_credential_requests = AgentEngineClient.extract_credential_requests

    monkeypatch.setattr(handler, "_agent_client", agent_client)
    monkeypatch.setattr(handler, "_session_manager", session_manager)
    monkeypatch.setattr(handler, "_discord_sender", discord_sender)

    return agent_client, session_manager, discord_sender


@pytest.mark.asyncio
async def test_notify_agent_suppressed_response_does_not_send(email_services):
    agent_client, _, discord_sender = email_services
    agent_client.send_message_events = AsyncMock(
        return_value=_text_events("<SUPPRESS RESPONSE>\nNo user update needed.")
    )

    await handler._notify_agent_of_emails(
        gmail_address="user@gmail.com",
        start_history_id="12345",
        user_id="user-123",
        rules_text="(no rules)",
    )

    discord_sender.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_agent_normal_response_routes_to_discord(email_services):
    agent_client, _, discord_sender = email_services
    agent_client.send_message_events = AsyncMock(
        return_value=_text_events("You have an invoice due tomorrow.")
    )

    await handler._notify_agent_of_emails(
        gmail_address="billing@example.com",
        start_history_id="12345",
        user_id="user-123",
        rules_text="(no rules)",
    )

    discord_sender.send.assert_awaited_once_with(
        "user-123", "You have an invoice due tomorrow."
    )


@pytest.mark.asyncio
async def test_notify_agent_prompt_includes_offline_safety_and_history_id(
    email_services,
):
    agent_client, _, _ = email_services
    agent_client.send_message_events = AsyncMock(return_value=_text_events("<SUPPRESS RESPONSE>"))

    await handler._notify_agent_of_emails(
        gmail_address="user@gmail.com",
        start_history_id="99999",
        user_id="user-123",
        rules_text="- Match [topic: newsletters] -> ignore",
    )

    prompt = agent_client.send_message_events.await_args.kwargs["message"]
    assert "OFFLINE MODE" in prompt
    assert "will be rejected" in prompt
    assert "<SUPPRESS RESPONSE>" in prompt
    assert "since_history_id=\"99999\"" in prompt
    assert "read_emails" in prompt


@pytest.mark.asyncio
async def test_notify_agent_confirmation_declines_and_forwards_fallback(email_services):
    agent_client, _, discord_sender = email_services
    agent_client.send_message_events = AsyncMock(return_value=_confirmation_events())
    agent_client.send_confirmation_response = AsyncMock(return_value=_text_events("I'll note that for you."))

    await handler._notify_agent_of_emails(
        gmail_address="user@gmail.com",
        start_history_id="12345",
        user_id="user-123",
        rules_text="(no rules)",
    )

    agent_client.send_confirmation_response.assert_awaited_once_with(
        user_id="user-123",
        session_id="agent-session-123",
        confirmation_function_call_id="confirm-1",
        confirmed=False,
    )
    discord_sender.send.assert_awaited_once_with("user-123", "I'll note that for you.")
