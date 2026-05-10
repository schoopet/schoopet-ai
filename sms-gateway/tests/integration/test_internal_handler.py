"""Tests for the internal task execution endpoints."""
import pytest
from unittest.mock import AsyncMock, MagicMock

import src.internal.handler as handler
from src.internal.handler import (
    ExecuteTaskRequest,
    execute_task,
    init_internal_services,
)

TASK_ID = "task-abc123"
USER_ID = "789673338217037825"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset all module-level globals before each test."""
    handler._agent_client = None
    handler._discord_sender = None
    handler._firestore_client = None
    handler._task_executor = None
    yield


@pytest.fixture
def agent_client():
    c = AsyncMock()
    c.send_message_events = AsyncMock(return_value=[])
    c.extract_text = MagicMock(return_value="Agent notification response")
    return c


@pytest.fixture
def discord_sender():
    return AsyncMock()


@pytest.fixture
def mock_firestore():
    client = MagicMock()
    doc_ref = MagicMock()
    doc_ref.update = AsyncMock()
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {}
    doc_ref.get = AsyncMock(return_value=doc)
    client.collection.return_value.document.return_value = doc_ref
    return client


class TestExecuteTask:
    """Gateway task execution endpoint delegates to the initialized executor."""

    @pytest.mark.asyncio
    async def test_execute_task_endpoint_runs_gateway_executor(
        self, agent_client, discord_sender, mock_firestore
    ):
        init_internal_services(
            agent_client=agent_client,
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )
        handler._task_executor.execute_task = AsyncMock(return_value={"success": True})

        response = await execute_task(
            request=MagicMock(),
            payload=ExecuteTaskRequest(task_id=TASK_ID, user_id=USER_ID),
            caller="svc",
        )

        assert response.status == "completed"
        handler._task_executor.execute_task.assert_awaited_once_with(TASK_ID)

    @pytest.mark.asyncio
    async def test_execute_task_endpoint_returns_failed_status(
        self, agent_client, discord_sender, mock_firestore
    ):
        init_internal_services(
            agent_client=agent_client,
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )
        handler._task_executor.execute_task = AsyncMock(
            return_value={"success": False, "error": "Agent crashed"}
        )

        response = await execute_task(
            request=MagicMock(),
            payload=ExecuteTaskRequest(task_id=TASK_ID, user_id=USER_ID),
            caller="svc",
        )

        assert response.status == "failed"
        assert response.message == "Agent crashed"
