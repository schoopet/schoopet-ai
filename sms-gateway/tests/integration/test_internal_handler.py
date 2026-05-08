"""Tests for the internal task completion and notification flow.

Covers:
- /internal/task-review delivers result directly to user (no agent review step)
- /internal/user-notify marks the task NOTIFIED in Firestore only after
  confirmed delivery (status race fix)
- Channel routing for Discord, Telegram, Slack, SMS
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, ANY

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
    c.send_message = AsyncMock(return_value="Agent notification response")
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
    .get() and .update() are awaitables.
    """
    client = MagicMock()
    doc_ref = MagicMock()
    doc_ref.update = AsyncMock()
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {"notification_channel": "sms"}
    doc_ref.get = AsyncMock(return_value=doc)
    client.collection.return_value.document.return_value = doc_ref
    return client


@pytest.fixture
def session_manager():
    mgr = AsyncMock()
    mgr.is_session_active = MagicMock(return_value=True)
    return mgr


def _user_session(channel="sms", session_id=USER_SESSION_ID):
    s = MagicMock()
    s.agent_session_id = session_id
    s.channel = channel
    return s


# ── /internal/task-review ─────────────────────────────────────────────────────


class TestTaskReview:
    """trigger_task_review must deliver the result directly to the user."""

    @pytest.mark.asyncio
    async def test_result_delivered_directly_when_no_session(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """Task result is sent directly to the user when no active session."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = TaskReviewRequest(task_id=TASK_ID, user_id=USER_ID, result="Research done.")
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        sms_sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_result_delivered_via_active_session(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """When user has an active session, result is routed through the agent."""
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

        payload = TaskReviewRequest(task_id=TASK_ID, user_id=USER_ID, result="Research done.")
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        agent_client.send_message.assert_awaited_once()
        call_args = agent_client.send_message.call_args
        assert "INTERNAL_TASK_COMPLETE" in call_args.kwargs["message"]
        sms_sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_task_notifies_user_with_error_message(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """Failed tasks send an error message directly to the user."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = TaskReviewRequest(
            task_id=TASK_ID, user_id=USER_ID, result=None, error="Agent crashed"
        )
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        sms_sender.send.assert_awaited_once()
        sent_body = sms_sender.send.call_args.kwargs.get("body") or sms_sender.send.call_args[1].get("body")
        assert "error" in sent_body.lower()
        assert "Agent crashed" in sent_body

    @pytest.mark.asyncio
    async def test_no_result_and_no_error_is_skipped(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """Tasks with neither result nor error produce no notification."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = TaskReviewRequest(task_id=TASK_ID, user_id=USER_ID, result=None, error=None)
        response = await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        sms_sender.send.assert_not_awaited()
        assert response.status == "skipped"

    @pytest.mark.asyncio
    async def test_notification_channel_read_from_firestore(
        self, agent_client, session_manager, telegram_sender, mock_firestore
    ):
        """notification_channel is read from the Firestore task doc, not the payload."""
        mock_firestore.collection.return_value.document.return_value.get = AsyncMock(
            return_value=MagicMock(
                exists=True,
                to_dict=MagicMock(return_value={"notification_channel": "telegram"})
            )
        )
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=AsyncMock(),
            telegram_sender=telegram_sender,
            firestore_client=mock_firestore,
        )

        payload = TaskReviewRequest(task_id=TASK_ID, user_id=USER_ID, result="Done.")
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        telegram_sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_task_notified_after_delivery(
        self, agent_client, session_manager, sms_sender, mock_firestore
    ):
        """Task is marked NOTIFIED in Firestore after successful delivery."""
        session_manager.get_user_session = AsyncMock(return_value=None)

        init_internal_services(
            session_manager=session_manager,
            agent_client=agent_client,
            sms_sender=sms_sender,
            firestore_client=mock_firestore,
        )

        payload = TaskReviewRequest(task_id=TASK_ID, user_id=USER_ID, result="Done.")
        await trigger_task_review(request=MagicMock(), payload=payload, caller="svc")

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.update.assert_awaited_once_with({
            "status": "notified",
            "notified_at": ANY,
        })


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
        """A user with an active Slack session is notified via the personal agent."""
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
