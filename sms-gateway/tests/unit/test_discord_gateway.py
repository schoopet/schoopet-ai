"""Unit tests for Discord gateway attachment handling."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
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
from src.session.approvals import PendingApprovalCoordinator


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
    session_manager.get_pending_credential = AsyncMock(return_value=None)
    session_manager.get_pending_approval = AsyncMock()
    session_manager.get_pending_approval_group = AsyncMock()
    session_manager.clear_pending_approval_group = AsyncMock()
    session_manager.set_pending_approval_group_message = AsyncMock()
    _coord = PendingApprovalCoordinator()
    session_manager.should_send_pending_approval_notification = _coord.should_send_notification
    session_manager.pending_approval_notification_id = _coord.notification_id
    session_manager.format_pending_approval_notification = _coord.format_notification
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
    pending = {
        "id": "pending-123",
        "tool_name": "send_email",
        "tool_args": {"to": "x@example.com", "subject": "Hi"},
        "approval_group_id": "session:agent-session-123:discord:",
        "approval_notification_id": "pending-123",
        "is_group_notification_owner": True,
    }
    session_manager.add_pending_approval = AsyncMock(return_value=pending)
    session_manager.get_pending_approval_group = AsyncMock(return_value=[pending])
    channel = SimpleNamespace(
        id="channel-123",
        send=AsyncMock(return_value=SimpleNamespace(id=123)),
    )

    await gateway._handle_gateway_message("user-123", "send it", channel)

    session_manager.add_pending_approval.assert_awaited_once()
    send_kwargs = channel.send.await_args.kwargs
    assert isinstance(send_kwargs["view"], _ConfirmationView)
    assert "Approve this action?" in channel.send.await_args.args[0]
    session_manager.set_pending_approval_group_message.assert_awaited_once_with(
        "user-123",
        "pending-123",
        approval_channel_id="channel-123",
        approval_message_id="123",
        session_scope="discord:dm:channel-123",
    )
    session_manager.update_last_activity.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_multiple_confirmations_shows_one_grouped_button_view(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    agent_client.send_message_events = AsyncMock(return_value=_two_confirmation_events())
    pending_1 = {
        "id": "pending-1",
        "tool_name": "create_calendar_event",
        "tool_args": {"title": "Lunch"},
        "approval_group_id": "session:agent-session-123:discord:",
        "approval_notification_id": "pending-1",
        "is_group_notification_owner": True,
    }
    pending_2 = {
        "id": "pending-2",
        "tool_name": "save_file_to_drive",
        "tool_args": {"filename": "notes.txt"},
        "approval_group_id": "session:agent-session-123:discord:",
        "approval_notification_id": "pending-1",
        "is_group_notification_owner": False,
    }
    session_manager.add_pending_approval = AsyncMock(side_effect=[pending_1, pending_2])
    session_manager.get_pending_approval_group = AsyncMock(return_value=[pending_1, pending_2])
    channel = SimpleNamespace(
        id="channel-123",
        send=AsyncMock(return_value=SimpleNamespace(id=123)),
    )

    await gateway._handle_gateway_message("user-123", "do both", channel)

    assert session_manager.add_pending_approval.await_count == 2
    # 1 status message ("> working...") + 1 grouped confirmation button view
    assert channel.send.await_count == 2
    confirmation_calls = [c for c in channel.send.await_args_list if "view" in c.kwargs]
    assert len(confirmation_calls) == 1
    call = confirmation_calls[0]
    assert isinstance(call.kwargs["view"], _ConfirmationView)
    assert "Approve all 2 actions?" in call.args[0]
    assert [item.label for item in call.kwargs["view"].children] == ["Approve All", "Reject All"]
    session_manager.update_last_activity.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_appends_confirmation_by_editing_existing_group_message(gateway_services):
    gateway, session_manager, _ = gateway_services
    pending = {
        "id": "pending-2",
        "tool_name": "save_file_to_drive",
        "tool_args": {"filename": "notes.txt"},
        "approval_group_id": "session:agent-session-123:discord:",
        "approval_notification_id": "pending-1",
        "approval_message_id": "123",
        "approval_channel_id": "channel-123",
    }
    pending_group = [
        {
            "id": "pending-1",
            "tool_name": "create_calendar_event",
            "tool_args": {"title": "Lunch"},
            "approval_group_id": "session:agent-session-123:discord:",
            "approval_notification_id": "pending-1",
            "is_group_notification_owner": True,
            "approval_message_id": "123",
            "approval_channel_id": "channel-123",
        },
        pending,
    ]
    existing_message = SimpleNamespace(edit=AsyncMock())
    channel = SimpleNamespace(
        id="channel-123",
        fetch_message=AsyncMock(return_value=existing_message),
        send=AsyncMock(),
    )
    session_manager.add_pending_approval = AsyncMock(return_value=pending)
    session_manager.get_pending_approval_group = AsyncMock(return_value=pending_group)

    await gateway._store_and_send_confirmations(
        user_id="user-123",
        confirmations=[object()],
        session_id="agent-session-123",
        channel=channel,
        session_scope="discord:dm:99999",
    )

    existing_message.edit.assert_awaited_once()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_sends_new_group_message_when_existing_edit_fails(gateway_services):
    gateway, session_manager, _ = gateway_services
    pending = {
        "id": "pending-2",
        "tool_name": "save_file_to_drive",
        "tool_args": {"filename": "notes.txt"},
        "approval_group_id": "session:agent-session-123:discord:",
        "approval_notification_id": "pending-1",
        "approval_message_id": "123",
        "approval_channel_id": "channel-123",
    }
    pending_group = [
        {
            "id": "pending-1",
            "tool_name": "create_calendar_event",
            "tool_args": {"title": "Lunch"},
            "approval_group_id": "session:agent-session-123:discord:",
            "approval_notification_id": "pending-1",
            "is_group_notification_owner": True,
            "approval_message_id": "123",
            "approval_channel_id": "channel-123",
        },
        pending,
    ]
    channel = SimpleNamespace(
        id="channel-123",
        fetch_message=AsyncMock(side_effect=RuntimeError("deleted")),
        send=AsyncMock(return_value=SimpleNamespace(id=456)),
    )
    session_manager.add_pending_approval = AsyncMock(return_value=pending)
    session_manager.get_pending_approval_group = AsyncMock(return_value=pending_group)

    await gateway._store_and_send_confirmations(
        user_id="user-123",
        confirmations=[object()],
        session_id="agent-session-123",
        channel=channel,
        session_scope="discord:dm:99999",
    )

    channel.send.assert_awaited_once()
    session_manager.set_pending_approval_group_message.assert_awaited_once_with(
        "user-123",
        "pending-1",
        approval_channel_id="channel-123",
        approval_message_id="456",
        session_scope="discord:dm:99999",
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
    session_manager.get_pending_approval = AsyncMock(return_value=pending)
    session_manager.get_pending_approval_group = AsyncMock(return_value=[pending])
    session_manager.clear_pending_approval_group = AsyncMock()
    agent_client.send_confirmation_responses_batch = AsyncMock(
        return_value=_text_events("Done.")
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
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

    agent_client.send_confirmation_responses_batch.assert_awaited_once_with(
        user_id="user-123",
        session_id="agent-session-123",
        confirmations=[("confirm-1", True)],
    )
    session_manager.clear_pending_approval_group.assert_awaited_once_with("user-123", "pending-123", session_scope="discord:dm:99999")
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once_with(view=view)
    interaction.followup.send.assert_awaited_once_with("Done.")
    assert all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_reject_callback_sends_false_confirmation(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    session_manager.get_pending_approval = AsyncMock(
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
    agent_client.send_confirmation_responses_batch = AsyncMock(return_value=[])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
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

    confirmations_arg = agent_client.send_confirmation_responses_batch.await_args.kwargs["confirmations"]
    assert all(confirmed is False for _, confirmed in confirmations_arg)
    session_manager.clear_pending_approval_group.assert_awaited_once_with("user-123", "pending-123", session_scope="discord:dm:99999")
    interaction.response.defer.assert_awaited_once()
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
    session_manager.get_pending_approval = AsyncMock(return_value=pending_a)
    session_manager.get_pending_approval_group = AsyncMock(
        return_value=[pending_a, pending_b]
    )
    session_manager.clear_pending_approval_group = AsyncMock()
    agent_client.send_confirmation_responses_batch = AsyncMock(
        return_value=_text_events("Done.")
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
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

    agent_client.send_confirmation_responses_batch.assert_awaited_once()
    call_kwargs = agent_client.send_confirmation_responses_batch.await_args.kwargs
    assert call_kwargs["session_id"] == "agent-session-123"
    assert call_kwargs["confirmations"] == [("confirm-a", True), ("confirm-b", True)]
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
    session_manager.get_pending_approval.assert_not_awaited()
    agent_client.send_confirmation_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_callback_defers_before_pending_lookup(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    calls: list[str] = []
    pending = {
        "id": "pending-123",
        "agent_session_id": "agent-session-123",
        "adk_confirmation_function_call_id": "confirm-1",
    }

    async def defer():
        calls.append("defer")

    async def get_pending_approval(*args, **kwargs):
        calls.append("get_pending_approval")
        return pending

    async def edit_original_response(**kwargs):
        calls.append("edit_original_response")

    session_manager.get_pending_approval = AsyncMock(side_effect=get_pending_approval)
    session_manager.get_pending_approval_group = AsyncMock(return_value=[pending])
    session_manager.clear_pending_approval_group = AsyncMock()
    agent_client.send_confirmation_responses_batch = AsyncMock(return_value=[])
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(side_effect=defer)),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(side_effect=edit_original_response),
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

    assert calls[:3] == ["defer", "edit_original_response", "get_pending_approval"]
    assert all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_approval_callback_returns_when_discord_interaction_expired(gateway_services):
    gateway, session_manager, agent_client = gateway_services
    response = MagicMock(status=404, reason="Not Found")
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="user-123"),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(
                side_effect=discord.NotFound(
                    response,
                    {"code": 10062, "message": "Unknown interaction"},
                )
            ),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    view = _ConfirmationView(gateway, "user-123", "pending-123")

    await gateway._resolve_confirmation_button(
        interaction=interaction,
        user_id="user-123",
        pending_id="pending-123",
        confirmed=True,
        view=view,
    )

    session_manager.get_pending_approval.assert_not_awaited()
    agent_client.send_confirmation_responses_batch.assert_not_awaited()


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
