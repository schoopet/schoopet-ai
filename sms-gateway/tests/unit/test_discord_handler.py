import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from src.discord import handler


class _FakeRequest:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {}

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def discord_handler_services(monkeypatch):
    session_manager = AsyncMock()
    agent_client = AsyncMock()
    discord_sender = AsyncMock()

    monkeypatch.setattr(handler, "_session_manager", session_manager)
    monkeypatch.setattr(handler, "_agent_client", agent_client)
    monkeypatch.setattr(handler, "_discord_sender", discord_sender)
    monkeypatch.setattr(handler, "_rate_limiter", None)
    monkeypatch.setattr(
        handler,
        "get_settings",
        lambda: SimpleNamespace(ENABLE_SIGNATURE_VALIDATION=False),
    )

    return session_manager, agent_client, discord_sender


@pytest.mark.asyncio
async def test_component_confirmation_acknowledges_and_schedules_resolution(
    discord_handler_services,
):
    session_manager, _, _ = discord_handler_services
    session_manager.get_pending_confirmation = AsyncMock(
        return_value={"id": "pending-123"}
    )
    background_tasks = BackgroundTasks()

    response = await handler.handle_discord_webhook(
        _FakeRequest(
            {
                "type": handler.INTERACTION_MESSAGE_COMPONENT,
                "token": "interaction-token",
                "data": {"custom_id": "pending-123:approve"},
                "user": {"id": "user-123"},
            }
        ),
        background_tasks,
    )

    body = json.loads(response.body)
    assert body["type"] == handler.RESPONSE_UPDATE_MESSAGE
    assert body["data"]["components"] == []
    assert "Approved" in body["data"]["content"]
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_component_confirmation_rejects_wrong_or_expired_pending(
    discord_handler_services,
):
    session_manager, _, _ = discord_handler_services
    session_manager.get_pending_confirmation = AsyncMock(return_value=None)

    response = await handler.handle_discord_webhook(
        _FakeRequest(
            {
                "type": handler.INTERACTION_MESSAGE_COMPONENT,
                "token": "interaction-token",
                "data": {"custom_id": "pending-123:approve"},
                "user": {"id": "other-user"},
            }
        ),
        BackgroundTasks(),
    )

    body = json.loads(response.body)
    assert body["type"] == handler.RESPONSE_CHANNEL_MESSAGE
    assert body["data"]["flags"] == handler.EPHEMERAL_FLAG
    assert "not for you" in body["data"]["content"]


@pytest.mark.asyncio
async def test_process_component_confirmation_sends_adk_response_and_followup(
    discord_handler_services,
):
    session_manager, agent_client, discord_sender = discord_handler_services
    session_manager.get_pending_confirmation = AsyncMock(
        return_value={
            "id": "pending-123",
            "agent_session_id": "agent-session-123",
            "adk_confirmation_function_call_id": "confirm-1",
        }
    )
    session_manager.clear_pending_confirmation = AsyncMock()
    session_manager.update_last_activity = AsyncMock()
    agent_client.send_confirmation_response = AsyncMock(return_value=["event"])
    agent_client.extract_text = lambda events: "Done."
    discord_sender.send_followup = AsyncMock()

    await handler.process_discord_confirmation_component(
        user_id="user-123",
        pending_id="pending-123",
        interaction_token="interaction-token",
        confirmed=True,
    )

    agent_client.send_confirmation_response.assert_awaited_once_with(
        user_id="user-123",
        session_id="agent-session-123",
        confirmation_function_call_id="confirm-1",
        confirmed=True,
    )
    session_manager.clear_pending_confirmation.assert_awaited_once_with("user-123")
    discord_sender.send_followup.assert_awaited_once_with("interaction-token", "Done.")
