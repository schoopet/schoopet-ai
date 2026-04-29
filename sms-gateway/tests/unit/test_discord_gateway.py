"""Unit tests for Discord gateway attachment handling."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.adk.events import Event
from google.genai import types

from src.agent.client import AgentEngineClient
from src.discord.gateway import (
    _ConfirmationView,
    FALLBACK_MIME_TYPE,
    MAX_INLINE_ATTACHMENT_BYTES,
    SchoopetGateway,
    _build_discord_message_content,
    _format_confirmation_prompt,
)


class _FakeAttachment:
    def __init__(self, filename: str, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self.size = len(data)

    async def read(self) -> bytes:
        return self._data


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
                                    "args": {"to": "x@example.com", "subject": "Hi"},
                                },
                                "toolConfirmation": {"hint": "Send email?"},
                            },
                        )
                    )
                ],
            ),
        )
    ]


@pytest.fixture
def gateway_services():
    session_manager = AsyncMock()
    session_manager.get_or_create_session = AsyncMock(
        return_value=SimpleNamespace(
            agent_session_id="agent-session-123",
            is_new_user=False,
            is_new_session=False,
        )
    )
    session_manager.update_last_activity = AsyncMock()
    agent_client = AsyncMock()
    agent_client.extract_text = AgentEngineClient.extract_text
    agent_client.extract_confirmation_requests = AgentEngineClient.extract_confirmation_requests
    gateway = SchoopetGateway(session_manager, agent_client)
    return gateway, session_manager, agent_client


@pytest.mark.asyncio
async def test_build_content_text_only():
    content = await _build_discord_message_content("hello", [])

    assert content.role == "user"
    assert len(content.parts) == 1
    assert content.parts[0].text == "hello"


@pytest.mark.asyncio
async def test_build_content_with_attachment():
    attachment = _FakeAttachment(
        filename="resume.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4",
    )

    content = await _build_discord_message_content("Please review this", [attachment])

    assert content.role == "user"
    assert len(content.parts) == 2
    assert "Please review this" in content.parts[0].text
    assert "resume.pdf" in content.parts[0].text
    assert content.parts[1].inline_data.mime_type == "application/pdf"
    assert content.parts[1].inline_data.data == b"%PDF-1.4"


@pytest.mark.asyncio
async def test_build_content_attachment_only():
    attachment = _FakeAttachment(
        filename="photo.png",
        content_type="image/png",
        data=b"\x89PNG",
    )

    content = await _build_discord_message_content("", [attachment])

    assert len(content.parts) == 2
    assert "no accompanying text" in content.parts[0].text
    assert "photo.png" in content.parts[0].text
    assert content.parts[1].inline_data.mime_type == "image/png"


@pytest.mark.asyncio
async def test_build_content_uses_fallback_mime_type():
    attachment = _FakeAttachment(
        filename="blob.bin",
        content_type=None,
        data=b"\x00\x01",
    )

    content = await _build_discord_message_content("binary", [attachment])

    assert content.parts[1].inline_data.mime_type == FALLBACK_MIME_TYPE


@pytest.mark.asyncio
async def test_build_content_rejects_oversized_attachment():
    attachment = _FakeAttachment(
        filename="large.pdf",
        content_type="application/pdf",
        data=b"x" * (MAX_INLINE_ATTACHMENT_BYTES + 1),
    )

    with pytest.raises(ValueError, match="too large"):
        await _build_discord_message_content("review", [attachment])


def test_format_confirmation_prompt_summarizes_tool_call():
    confirmation = AgentEngineClient.extract_confirmation_requests(_confirmation_events())[0]

    prompt = _format_confirmation_prompt(confirmation)

    assert prompt.startswith("Approve this action?\nsend_email(")
    assert "x@example.com" in prompt


@pytest.mark.asyncio
async def test_gateway_confirmation_request_stores_pending_and_sends_button_view(
    gateway_services,
):
    gateway, session_manager, agent_client = gateway_services
    agent_client.send_message_events = AsyncMock(return_value=_confirmation_events())
    session_manager.set_pending_confirmation = AsyncMock(
        return_value={"id": "pending-123"}
    )
    channel = SimpleNamespace(send=AsyncMock())

    await gateway._handle_gateway_message("user-123", "send it", channel)

    session_manager.set_pending_confirmation.assert_awaited_once()
    send_kwargs = channel.send.await_args.kwargs
    assert isinstance(send_kwargs["view"], _ConfirmationView)
    assert "Approve this action?" in channel.send.await_args.args[0]
    session_manager.update_last_activity.assert_awaited_once_with(
        "user-123", channel="discord"
    )


@pytest.mark.asyncio
async def test_approve_callback_sends_confirmation_clears_pending_and_sends_result(
    gateway_services,
):
    gateway, session_manager, agent_client = gateway_services
    pending = {
        "id": "pending-123",
        "agent_session_id": "agent-session-123",
        "adk_confirmation_function_call_id": "confirm-1",
    }
    session_manager.get_pending_confirmation = AsyncMock(return_value=pending)
    session_manager.clear_pending_confirmation = AsyncMock()
    agent_client.send_confirmation_response = AsyncMock(
        return_value=_text_events("Done.")
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    view = _ConfirmationView(gateway, "user-123", "pending-123")

    await gateway._resolve_confirmation_button(
        interaction=interaction,
        user_id="user-123",
        pending_id="pending-123",
        confirmed=True,
        view=view,
    )

    agent_client.send_confirmation_response.assert_awaited_once_with(
        user_id="user-123",
        session_id="agent-session-123",
        confirmation_function_call_id="confirm-1",
        confirmed=True,
    )
    session_manager.clear_pending_confirmation.assert_awaited_once_with("user-123")
    interaction.response.edit_message.assert_awaited_once_with(view=view)
    interaction.followup.send.assert_awaited_once_with("Done.")
    assert all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_reject_callback_sends_false_confirmation(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    session_manager.get_pending_confirmation = AsyncMock(
        return_value={
            "id": "pending-123",
            "agent_session_id": "agent-session-123",
            "adk_confirmation_function_call_id": "confirm-1",
        }
    )
    session_manager.clear_pending_confirmation = AsyncMock()
    agent_client.send_confirmation_response = AsyncMock(return_value=[])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    view = _ConfirmationView(gateway, "user-123", "pending-123")

    await gateway._resolve_confirmation_button(
        interaction=interaction,
        user_id="user-123",
        pending_id="pending-123",
        confirmed=False,
        view=view,
    )

    assert agent_client.send_confirmation_response.await_args.kwargs["confirmed"] is False
    session_manager.clear_pending_confirmation.assert_awaited_once_with("user-123")
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_button_click_by_different_user_is_rejected(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="other-user"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    view = _ConfirmationView(gateway, "user-123", "pending-123")

    await gateway._resolve_confirmation_button(
        interaction=interaction,
        user_id="user-123",
        pending_id="pending-123",
        confirmed=True,
        view=view,
    )

    interaction.response.send_message.assert_awaited_once_with(
        "This approval is not for you.",
        ephemeral=True,
    )
    session_manager.get_pending_confirmation.assert_not_awaited()
    agent_client.send_confirmation_response.assert_not_awaited()
