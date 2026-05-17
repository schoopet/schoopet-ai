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
)
from src.discord.context import build_discord_context


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


def _confirmation_events(
    name: str = "send_email",
    call_id: str = "confirm-1",
    tool_id: str = "tool-1",
) -> list[Event]:
    return [
        Event(
            author="agent",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="adk_request_confirmation",
                            id=call_id,
                            args={
                                "originalFunctionCall": {
                                    "id": tool_id,
                                    "name": name,
                                    "args": {"to": "x@example.com", "subject": "Hi"},
                                },
                                "toolConfirmation": {"hint": "Approve?"},
                            },
                        )
                    )
                ],
            ),
        )
    ]


def _two_confirmation_events() -> list[Event]:
    """Two adk_request_confirmation calls in a single event stream."""
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
                                    "name": "create_calendar_event",
                                    "args": {"title": "Meeting"},
                                },
                                "toolConfirmation": {"hint": "Create event?"},
                            },
                        )
                    ),
                    types.Part(
                        function_call=types.FunctionCall(
                            name="adk_request_confirmation",
                            id="confirm-2",
                            args={
                                "originalFunctionCall": {
                                    "id": "tool-2",
                                    "name": "save_file_to_drive",
                                    "args": {"filename": "notes.txt"},
                                },
                                "toolConfirmation": {"hint": "Save file?"},
                            },
                        )
                    ),
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
    session_manager.get_pending_approval_group = AsyncMock()
    session_manager.clear_pending_approval_group = AsyncMock()
    session_manager.should_send_pending_approval_notification = lambda group: any(
        pending.get("is_group_notification_owner", True) for pending in group
    )
    session_manager.pending_approval_notification_id = lambda group: (
        group[0].get("approval_notification_id") or group[0]["id"]
    )
    session_manager.format_pending_approval_notification = lambda group: (
        f"Approve this action?\n{group[0].get('tool_name', 'unknown_tool')}()"
        if len(group) == 1
        else f"Approve {len(group)} action(s)"
    )
    agent_client = AsyncMock()
    agent_client.extract_text = AgentEngineClient.extract_text
    agent_client.extract_confirmation_requests = AgentEngineClient.extract_confirmation_requests
    agent_client.extract_credential_requests = AgentEngineClient.extract_credential_requests

    async def _resolve_passthrough(user_id, session_id, events, context=""):
        return (events, None)

    agent_client.resolve_iam_credential_events = _resolve_passthrough
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


@pytest.mark.asyncio
async def test_gateway_confirmation_request_stores_pending_and_sends_button_view(
    gateway_services,
):
    gateway, session_manager, agent_client = gateway_services
    agent_client.send_message_events = AsyncMock(return_value=_confirmation_events())
    session_manager.add_pending_approval = AsyncMock(
        return_value={
            "id": "pending-123",
            "tool_name": "send_email",
            "is_group_notification_owner": True,
        }
    )
    channel = SimpleNamespace(send=AsyncMock())

    await gateway._handle_gateway_message("user-123", "send it", channel)

    session_manager.add_pending_approval.assert_awaited_once()
    send_kwargs = channel.send.await_args.kwargs
    assert isinstance(send_kwargs["view"], _ConfirmationView)
    assert "Approve this action?" in channel.send.await_args.args[0]
    session_manager.update_last_activity.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_multiple_confirmations_shows_all_buttons(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    agent_client.send_message_events = AsyncMock(return_value=_two_confirmation_events())
    session_manager.add_pending_approval = AsyncMock(
        side_effect=[
            {"id": "pending-1", "tool_name": "create_calendar_event"},
            {"id": "pending-2", "tool_name": "save_file_to_drive"},
        ]
    )
    channel = SimpleNamespace(send=AsyncMock())

    await gateway._handle_gateway_message("user-123", "do both", channel)

    assert session_manager.add_pending_approval.await_count == 2
    assert channel.send.await_count == 2
    for call in channel.send.await_args_list:
        assert isinstance(call.kwargs["view"], _ConfirmationView)
    session_manager.update_last_activity.assert_awaited_once()


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
    session_manager.get_pending_approval_group = AsyncMock(return_value=[pending])
    session_manager.clear_pending_approval_group = AsyncMock()
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
        session_scope="discord:dm:99999",
    )

    agent_client.send_confirmation_response.assert_awaited_once_with(
        user_id="user-123",
        session_id="agent-session-123",
        confirmation_function_call_id="confirm-1",
        confirmed=True,
    )
    session_manager.clear_pending_approval_group.assert_awaited_once_with("user-123", "pending-123", session_scope="discord:dm:99999")
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
    session_manager.get_pending_approval_group = AsyncMock(
        return_value=[
            {
                "id": "pending-123",
                "agent_session_id": "agent-session-123",
                "adk_confirmation_function_call_id": "confirm-1",
            }
        ]
    )
    session_manager.clear_pending_approval_group = AsyncMock()
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
        session_scope="discord:dm:99999",
    )

    assert agent_client.send_confirmation_response.await_args.kwargs["confirmed"] is False
    session_manager.clear_pending_approval_group.assert_awaited_once_with("user-123", "pending-123", session_scope="discord:dm:99999")
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_callback_resolves_entire_pending_group(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    pending_a = {
        "id": "pending-a",
        "agent_session_id": "agent-session-123",
        "adk_confirmation_function_call_id": "confirm-a",
    }
    pending_b = {
        "id": "pending-b",
        "agent_session_id": "agent-session-123",
        "adk_confirmation_function_call_id": "confirm-b",
    }
    session_manager.get_pending_confirmation = AsyncMock(return_value=pending_a)
    session_manager.get_pending_approval_group = AsyncMock(
        return_value=[pending_a, pending_b]
    )
    session_manager.clear_pending_approval_group = AsyncMock()
    agent_client.send_confirmation_response = AsyncMock(
        side_effect=[[], _text_events("Done.")]
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    view = _ConfirmationView(gateway, "user-123", "pending-a")

    await gateway._resolve_confirmation_button(
        interaction=interaction,
        user_id="user-123",
        pending_id="pending-a",
        confirmed=True,
        view=view,
        session_scope="discord:dm:99999",
    )

    assert agent_client.send_confirmation_response.await_count == 2
    assert [
        call.kwargs["confirmation_function_call_id"]
        for call in agent_client.send_confirmation_response.await_args_list
    ] == ["confirm-a", "confirm-b"]
    session_manager.clear_pending_approval_group.assert_awaited_once_with("user-123", "pending-a", session_scope="discord:dm:99999")


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


@pytest.mark.asyncio
async def test_gateway_scopes_session_and_prefixes_message_context(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    agent_client.send_message_events = AsyncMock(return_value=_text_events("Done."))
    channel = SimpleNamespace(
        id="channel-456",
        name="project-alpha",
        guild=SimpleNamespace(id="guild-123"),
        send=AsyncMock(),
    )

    await gateway._handle_gateway_message(
        "user-123",
        "What did we decide?",
        channel,
        discord_context=build_discord_context(
            channel_id="channel-456",
            guild_id="guild-123",
            channel_name="project-alpha",
        ),
    )

    session_manager.get_or_create_session.assert_awaited_once_with(
        "user-123",
        channel="discord",
        session_scope="discord:guild:guild-123:channel:channel-456",
        state_extra={
            "discord_guild_id": "guild-123",
            "discord_channel_id": "channel-456",
            "discord_channel_name": "project-alpha",
        },
    )
    sent_message = agent_client.send_message_events.await_args.kwargs["message"]
    assert "session_scope: discord:guild:guild-123:channel:channel-456" in sent_message
    assert "channel_name: project-alpha" in sent_message
    assert "User message:\nWhat did we decide?" in sent_message
    session_manager.update_last_activity.assert_awaited_once_with(
        "user-123",
        channel="discord",
        session_scope="discord:guild:guild-123:channel:channel-456",
    )
