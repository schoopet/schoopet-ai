"""Tests for the internal task completion and notification flow.

Covers:
- /internal/user-notify marks the task NOTIFIED in Firestore only after
  confirmed delivery (status race fix)
- Channel routing through Discord
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, ANY

import src.internal.handler as handler
from src.internal.handler import (
    ExecuteTaskRequest,
    UserNotifyRequest,
    execute_task,
    notify_user,
    _mark_task_notified,
    init_internal_services,
)

TASK_ID = "task-abc123"
USER_ID = "789673338217037825"
USER_SESSION_ID = "user-sess-111"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset all module-level globals before each test."""
    handler._session_manager = None
    handler._agent_client = None
    handler._discord_sender = None
    handler._firestore_client = None
    handler._task_executor = None
    yield


@pytest.fixture
def agent_client():
    c = AsyncMock()
    c.send_message_events = AsyncMock(return_value=[])
    c.extract_confirmation_requests = MagicMock(return_value=[])
    c.extract_text = MagicMock(return_value="Agent notification response")
    return c


@pytest.fixture
def discord_sender():
    return AsyncMock()


@pytest.fixture
def mock_firestore():
    """Firestore AsyncClient mock."""
    client = MagicMock()
    doc_ref = MagicMock()
    doc_ref.update = AsyncMock()
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {}
    doc_ref.get = AsyncMock(return_value=doc)
    client.collection.return_value.document.return_value = doc_ref
    return client


@pytest.fixture
def session_manager():
    mgr = AsyncMock()
    mgr.is_session_active = MagicMock(return_value=True)
    return mgr


def _user_session(channel="discord", session_id=USER_SESSION_ID):
    s = MagicMock()
    s.agent_session_id = session_id
    s.channel = channel
    return s


class TestExecuteTask:
    """Gateway task execution endpoint delegates to the initialized executor."""

    @pytest.mark.asyncio
    async def test_execute_task_endpoint_runs_gateway_executor(
        self, agent_client, session_manager, discord_sender, mock_firestore
    ):
        init_internal_services(
            session_manager=session_manager,
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
        self, agent_client, session_manager, discord_sender, mock_firestore
    ):
        init_internal_services(
            session_manager=session_manager,
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

# ── /internal/user-notify ────────────────────────────────────────────────────


class TestUserNotify:
    """notify_user must deliver via the right channel and mark the task NOTIFIED."""

    @pytest.mark.asyncio
    async def test_delivers_via_agent_when_session_active(
        self, agent_client, session_manager, discord_sender, mock_firestore
    ):
        """Active session → message routed through the agent, not directly."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="discord")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message_events = AsyncMock(return_value=[])
        agent_client.extract_text = MagicMock(return_value="Here's your result!")

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Research done."
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message_events.assert_awaited_once()
        discord_sender.send.assert_awaited_once_with(USER_ID, "Here's your result!")

    @pytest.mark.asyncio
    async def test_marks_notified_after_active_session_delivery(
        self, agent_client, session_manager, discord_sender, mock_firestore
    ):
        """NOTIFIED status must be written to Firestore after delivery via session."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="discord")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message_events = AsyncMock(return_value=[])
        agent_client.extract_text = MagicMock(return_value="Result delivered.")

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done."
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })

    @pytest.mark.asyncio
    async def test_delivers_directly_when_no_active_session(
        self, agent_client, session_manager, discord_sender, mock_firestore
    ):
        """No active session → raw message sent directly, no agent call."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Your task is done."
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_not_awaited()
        discord_sender.send.assert_awaited_once_with(USER_ID, "Your task is done.")

    @pytest.mark.asyncio
    async def test_marks_notified_after_direct_delivery(
        self, session_manager, discord_sender, mock_firestore
    ):
        """NOTIFIED status must also be written when there is no active session."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=AsyncMock(),
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done."
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })

    @pytest.mark.asyncio
    async def test_empty_agent_response_falls_back_to_direct_send(
        self, agent_client, session_manager, discord_sender, mock_firestore
    ):
        """If the agent returns no text, fall back to sending the raw message directly."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="discord")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message_events = AsyncMock(return_value=[])
        agent_client.extract_text = MagicMock(return_value="")  # empty response

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done."
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        # Raw message sent directly as fallback
        discord_sender.send.assert_awaited_once_with(USER_ID, "Done.")
        # Task still marked notified after direct send
        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })

    @pytest.mark.asyncio
    async def test_routes_to_discord(
        self, session_manager, discord_sender, mock_firestore
    ):
        """Discord channel → delivered via discord_sender."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=AsyncMock(),
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done."
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        discord_sender.send.assert_awaited_once_with(USER_ID, "Done.")


# ── _mark_task_notified ───────────────────────────────────────────────────────


class TestMarkTaskNotified:
    """_mark_task_notified must write the correct Firestore update."""

    @pytest.mark.asyncio
    async def test_updates_correct_document(self, mock_firestore):
        """Should update async_tasks/{task_id} with status=notified."""
        handler._firestore_client = mock_firestore

        await _mark_task_notified(TASK_ID)

        mock_firestore.collection.assert_called_with("async_tasks")
        mock_firestore.collection.return_value.document.assert_called_with(TASK_ID)
        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })

    @pytest.mark.asyncio
    async def test_does_nothing_when_no_firestore(self):
        """Should silently skip if Firestore client is not initialized."""
        handler._firestore_client = None
        await _mark_task_notified(TASK_ID)

    @pytest.mark.asyncio
    async def test_does_nothing_for_empty_task_id(self, mock_firestore):
        """Should silently skip for empty task_id."""
        handler._firestore_client = mock_firestore
        await _mark_task_notified("")
        mock_firestore.collection.assert_not_called()
