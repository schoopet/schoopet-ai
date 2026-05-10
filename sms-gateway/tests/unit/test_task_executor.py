"""Unit tests for gateway-owned async task execution."""
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from src.internal.task_executor import (
    GatewayTaskExecutor,
    build_allowed_resource_state,
)

TASK_ID = "task-123"
USER_ID = "user-123"


def _task_doc(status="pending", **overrides):
    data = {
        "task_id": TASK_ID,
        "user_id": USER_ID,
        "status": status,
        "task_type": "research",
        "instruction": "Research AI",
        "context": {"topic": "AI"},
        "allowed_resource_ids": ["sheet-1", "doc-1"],
        "notification_channel": "discord",
        "notification_session_scope": "discord:guild:g1:channel:c1",
        "notification_target_type": "discord_channel",
        "discord_channel_id": "c1",
        "discord_channel_name": "project-alpha",
    }
    data.update(overrides)
    doc = MagicMock()
    doc.exists = True
    doc.update_time = object()
    doc.to_dict.return_value = data
    return doc


@pytest.fixture
def firestore_client():
    client = MagicMock()
    doc_ref = MagicMock()
    doc_ref.get = AsyncMock(return_value=_task_doc())
    doc_ref.update = AsyncMock()
    client.collection.return_value.document.return_value = doc_ref
    return client


@pytest.fixture
def agent_client():
    client = AsyncMock()
    client.create_session = AsyncMock(return_value="task-session-1")
    client.send_message_events = AsyncMock(return_value=["event"])
    client.delete_session = AsyncMock()
    client.extract_text = MagicMock(return_value="Task result")
    return client


@pytest.fixture
def discord_sender():
    sender = AsyncMock()
    sender.send_channel = AsyncMock()
    sender.send = AsyncMock()
    return sender


@pytest.fixture
def executor(firestore_client, agent_client, discord_sender):
    return GatewayTaskExecutor(
        firestore_client=firestore_client,
        agent_client=agent_client,
        discord_sender=discord_sender,
    )


class TestAllowedResourceState:
    def test_builds_state_from_allowed_resource_ids(self):
        assert build_allowed_resource_state(["sheet-1", "doc-1"]) == {
            "_resource_confirmed_sheet-1": True,
            "_resource_confirmed_doc-1": True,
        }

    def test_empty_allowed_resources(self):
        assert build_allowed_resource_state([]) == {}


class TestGatewayTaskExecutor:
    @pytest.mark.asyncio
    async def test_execute_task_success(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        doc_ref = firestore_client.collection.return_value.document.return_value
        running_update = doc_ref.update.await_args_list[0]
        assert running_update.args[0] == {"status": "running", "started_at": ANY}
        assert "option" in running_update.kwargs
        doc_ref.update.assert_any_await({
            "status": "completed",
            "result": "Task result",
            "completed_at": ANY,
        })
        discord_sender.send_channel.assert_awaited_once_with("c1", "Task result")
        discord_sender.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_task_creates_and_retires_ephemeral_session(
        self, executor, agent_client
    ):
        await executor.execute_task(TASK_ID)

        agent_client.create_session.assert_awaited_once_with(
            user_id=USER_ID,
            state={
                "_resource_confirmed_sheet-1": True,
                "_resource_confirmed_doc-1": True,
                "channel": "discord",
                "session_scope": "discord:guild:g1:channel:c1",
                "discord_channel_id": "c1",
                "discord_channel_name": "project-alpha",
            },
        )
        agent_client.send_message_events.assert_awaited_once()
        prompt = agent_client.send_message_events.await_args.kwargs["message"]
        assert "Execute this research task:" in prompt
        assert "Instruction:\nResearch AI" in prompt
        assert "  topic: AI" in prompt
        agent_client.delete_session.assert_awaited_once_with(
            user_id=USER_ID,
            session_id="task-session-1",
        )

    @pytest.mark.asyncio
    async def test_ignores_already_processed_task(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.get = AsyncMock(return_value=_task_doc(status="completed"))

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        assert "Task already in status: completed" == result["message"]
        doc_ref.update.assert_not_awaited()
        agent_client.create_session.assert_not_awaited()
        discord_sender.send_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_task_returns_error(
        self, executor, firestore_client, agent_client
    ):
        missing = MagicMock()
        missing.exists = False
        firestore_client.collection.return_value.document.return_value.get = AsyncMock(
            return_value=missing
        )

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is False
        assert "not found" in result["error"]
        agent_client.create_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_stores_error_and_posts_to_channel(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        agent_client.send_message_events = AsyncMock(side_effect=Exception("Agent crashed"))

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is False
        assert "Agent crashed" in result["error"]
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.update.assert_any_await({
            "status": "failed",
            "error": "Agent crashed",
            "completed_at": ANY,
        })
        discord_sender.send_channel.assert_awaited_once()
        channel_id, text = discord_sender.send_channel.await_args.args
        assert channel_id == "c1"
        assert "Agent crashed" in text
        agent_client.delete_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_discord_channel_marks_failed_without_dm_fallback(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.get = AsyncMock(return_value=_task_doc(discord_channel_id=""))

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is False
        assert "discord_channel_id" in result["error"]
        agent_client.create_session.assert_not_awaited()
        discord_sender.send_channel.assert_not_awaited()
        discord_sender.send.assert_not_awaited()
