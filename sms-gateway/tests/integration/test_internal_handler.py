"""Tests for the internal task review and notification flow.

Covers:
- /internal/task-review sends to the single agent client regardless of payload.agent_type
- /internal/user-notify marks the task NOTIFIED in Firestore only after
  confirmed delivery (status race fix)
- Channel routing for Discord, Telegram, Slack, SMS
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, ANY
from datetime import datetime, timezone

import src.internal.handler as handler
from src.internal.handler import (
    TaskReviewRequest,
    UserNotifyRequest,
    trigger_task_review,
    notify_user,
    _mark_task_notified,
    init_internal_services,
)

TASK_ID = "task-abc123"
USER_ID = "+14155550001"
SUPERVISOR_SESSION_ID = "supervisor-sess-333"
USER_SESSION_ID = "user-sess-111"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset all module-level globals before each test."""
    handler._session_manager = None
    handler._agent_client = None
    handler._sms_sender = None
    handler._telegram_sender = None
    handler._slack_sender = None
    handler._discord_sender = None
    handler._firestore_client = None
    yield


@pytest.fixture
def agent_client():
    c = AsyncMock()
    c.send_message = AsyncMock(return_value="Agent review response")
    return c


@pytest.fixture
def sms_sender():
    return AsyncMock()


@pytest.fixture
def discord_sender():
    return AsyncMock()


@pytest.fixture
def telegram_sender():
    return AsyncMock()


@pytest.fixture
def slack_sender():
    return AsyncMock()


@pytest.fixture
def mock_firestore():
    """Firestore AsyncClient mock.

    .collection() and .document() are sync calls that return MagicMock.
    .update() is the only awaitable, so it uses AsyncMock.
    """
    client = MagicMock()
    doc_ref = MagicMock()
    doc_ref.update = AsyncMock()
    client.collection.return_value.document.return_value = doc_ref
    return client


@pytest.fixture
def session_manager():
    mgr = AsyncMock()
    mgr.is_session_active = MagicMock(return_value=True)
    return mgr


def _supervisor_session(session_id=SUPERVISOR_SESSION_ID):
    s = MagicMock()
    s.agent_session_id = session_id
    return s


def _user_session(channel="sms", session_id=USER_SESSION_ID):
    s = MagicMock()
    s.agent_session_id = session_id
    s.channel = channel
    return s


# ── /internal/task-review ─────────────────────────────────────────────────────


class TestTaskReview:
    """trigger_task_review must route to the single agent client."""

    @pytest.mark.asyncio
    async def test_personal_task_goes_to_agent_client(
        self, agent_client, session_manager
    ):
        """A task with agent_type='personal' uses the agent client."""
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session()
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, agent_type="personal", result="done"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_team_task_also_goes_to_agent_client(
        self, agent_client, session_manager
    ):
        """A task with agent_type='team' (legacy field) also uses the single agent client."""
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session()
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, agent_type="team", result="done"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_type_in_payload_is_ignored(
        self, agent_client, session_manager
    ):
        """agent_type in payload is kept for backward compat but ignored — same
        single agent handles all tasks regardless of value."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="slack")
        )
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session()
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, agent_type="personal", result="done"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_supervisor_session_created_without_agent_type(
        self, agent_client, session_manager
    ):
        """Supervisor session is created with phone_number and task_id only."""
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session()
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, agent_type="team", result="done"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        session_manager.get_supervisor_session.assert_awaited_once_with(
            phone_number=USER_ID,
            task_id=TASK_ID,
        )

    @pytest.mark.asyncio
    async def test_review_message_sent_to_supervisor_session(
        self, agent_client, session_manager
    ):
        """Review message must go to the supervisor session ID, not the user session."""
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session(SUPERVISOR_SESSION_ID)
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, agent_type="personal", result="Task result here"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        call_kwargs = agent_client.send_message.call_args.kwargs
        assert call_kwargs["session_id"] == SUPERVISOR_SESSION_ID
        assert "_supervisor" in call_kwargs["user_id"]
        assert "INTERNAL_TASK_REVIEW" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_failed_task_included_in_review_message(
        self, agent_client, session_manager
    ):
        """Error details must appear in the review message when the task failed."""
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session()
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, agent_type="personal",
            result=None, error="Agent crashed"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        message = agent_client.send_message.call_args.kwargs["message"]
        assert "FAILED" in message
        assert "Agent crashed" in message

    @pytest.mark.asyncio
    async def test_defaults_to_personal_when_agent_type_omitted(
        self, agent_client, session_manager
    ):
        """Missing agent_type field defaults to 'personal' (backward compat field)."""
        session_manager.get_supervisor_session = AsyncMock(
            return_value=_supervisor_session()
        )
        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
        )

        # agent_type not provided → defaults to "personal"
        payload = TaskReviewRequest(task_id=TASK_ID, user_id=USER_ID, result="done")
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()


# ── /internal/user-notify ────────────────────────────────────────────────────


class TestUserNotify:
    """notify_user must deliver via the right channel and mark the task NOTIFIED."""

    @pytest.mark.asyncio
    async def test_delivers_via_agent_when_session_active(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """Active session → message routed through the agent, not directly."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="sms")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message = AsyncMock(return_value="Here's your result!")

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Research done.", channel="sms"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()
        # Should deliver the agent's response, not the raw payload message
        sent_body = sms_sender.send.call_args.kwargs.get("body") or sms_sender.send.call_args[1].get("body")
        assert sent_body == "Here's your result!"

    @pytest.mark.asyncio
    async def test_marks_notified_after_active_session_delivery(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """NOTIFIED status must be written to Firestore after delivery via session."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="sms")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message = AsyncMock(return_value="Result delivered.")

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="sms"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })

    @pytest.mark.asyncio
    async def test_delivers_directly_when_no_active_session(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """No active session → raw message sent directly, no agent call."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Your task is done.", channel="sms"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_not_awaited()
        sms_sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_notified_after_direct_delivery(
        self, session_manager, sms_sender, mock_firestore
    ):
        """NOTIFIED status must also be written when there is no active session."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=AsyncMock(),
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="sms"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })

    @pytest.mark.asyncio
    async def test_does_not_mark_notified_when_agent_returns_empty(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """If the agent returns no response, message is not sent and task is not marked notified."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="sms")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message = AsyncMock(return_value="")  # empty response

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="sms"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        sms_sender.send.assert_not_awaited()
        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_routes_to_discord(
        self, session_manager, discord_sender, mock_firestore
    ):
        """Discord channel → delivered via discord_sender."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=AsyncMock(),
            sms_sender=AsyncMock(),
            discord_sender=discord_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="discord"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        discord_sender.send.assert_awaited_once_with(USER_ID, "Done.")

    @pytest.mark.asyncio
    async def test_routes_to_telegram(
        self, session_manager, telegram_sender, mock_firestore
    ):
        """Telegram channel → delivered via telegram_sender."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=AsyncMock(),
            sms_sender=AsyncMock(),
            telegram_sender=telegram_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="telegram"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        telegram_sender.send.assert_awaited_once_with(USER_ID, "Done.")

    @pytest.mark.asyncio
    async def test_routes_to_slack(
        self, session_manager, slack_sender, mock_firestore
    ):
        """Slack channel → delivered via slack_sender."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=AsyncMock(),
            sms_sender=AsyncMock(),
            slack_sender=slack_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="slack"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        slack_sender.send.assert_awaited_once_with(USER_ID, "Done.")

    @pytest.mark.asyncio
    async def test_slack_session_routes_through_personal_agent(
        self, agent_client, session_manager, mock_firestore
    ):
        """A user with an active Slack session is notified via the personal agent
        (Slack now routes to personal agent, not a separate team agent)."""
        session_manager.get_user_session = AsyncMock(
            return_value=_user_session(channel="slack")
        )
        session_manager.is_session_active = MagicMock(return_value=True)
        agent_client.send_message = AsyncMock(return_value="Result delivered.")
        slack_sender = AsyncMock()

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
            slack_sender=slack_sender,
            firestore_client=mock_firestore,
        )

        payload = UserNotifyRequest(
            user_id=USER_ID, task_id=TASK_ID, message="Done.", channel="slack"
        )
        await notify_user(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()
        slack_sender.send.assert_awaited_once()


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
        # Should not raise
        await _mark_task_notified(TASK_ID)

    @pytest.mark.asyncio
    async def test_does_nothing_for_empty_task_id(self, mock_firestore):
        """Should silently skip for empty task_id."""
        handler._firestore_client = mock_firestore
        await _mark_task_notified("")
        mock_firestore.collection.assert_not_called()
