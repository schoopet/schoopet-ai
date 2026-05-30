"""Unit tests for gateway-owned async task execution."""
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

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
    doc_ref.set = AsyncMock()
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
    client.extract_confirmation_requests = MagicMock(return_value=[])
    client.send_confirmation_responses_batch = AsyncMock(return_value=["follow-up-event"])
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
        doc_ref.update.assert_any_await({
            "status": "notified",
            "notified_at": ANY,
        })
        discord_sender.send_channel.assert_awaited_once_with("c1", "Task result")
        discord_sender.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_task_creates_ephemeral_session(
        self, executor, agent_client
    ):
        await executor.execute_task(TASK_ID)

        agent_client.create_session.assert_awaited_once_with(
            user_id=USER_ID,
            state={
                "_resource_confirmed_sheet-1": True,
                "_resource_confirmed_doc-1": True,
                "_offline_mode": True,
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
        agent_client.delete_session.assert_not_awaited()

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
    async def test_timeout_resets_to_pending_and_signals_retry(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        agent_client.send_message_events = AsyncMock(side_effect=TimeoutError())

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is False
        assert result["retryable"] is True
        assert "timeout" in result["error"].lower()
        doc_ref = firestore_client.collection.return_value.document.return_value
        # Task must be reset to pending so Cloud Tasks can retry it
        doc_ref.update.assert_any_await({"status": "pending", "started_at": ANY})
        # No failed status written, no Discord error notification sent
        statuses = [c.args[0].get("status") for c in doc_ref.update.await_args_list]
        assert "failed" not in statuses
        discord_sender.send_channel.assert_not_awaited()

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
        agent_client.delete_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_agent_response_marks_failed_and_notifies(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        agent_client.extract_text = MagicMock(return_value="")

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is False
        assert "empty response" in result["error"].lower()
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.update.assert_any_await({
            "status": "failed",
            "error": ANY,
            "completed_at": ANY,
        })
        discord_sender.send_channel.assert_awaited_once()
        channel_id, text = discord_sender.send_channel.await_args.args
        assert channel_id == "c1"
        assert "error" in text.lower()
        agent_client.delete_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discord_delivery_failure_preserves_completed_status(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        discord_sender.send_channel = AsyncMock(side_effect=Exception("Discord API error"))

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.update.assert_any_await({
            "status": "completed",
            "result": "Task result",
            "completed_at": ANY,
        })
        statuses_written = [
            call.args[0].get("status")
            for call in doc_ref.update.await_args_list
            if "status" in call.args[0]
        ]
        assert "failed" not in statuses_written

    @pytest.mark.asyncio
    async def test_missing_discord_channel_falls_back_to_dm(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.get = AsyncMock(return_value=_task_doc(discord_channel_id=""))

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        discord_sender.send_channel.assert_not_awaited()
        discord_sender.send.assert_awaited_once_with(USER_ID, "Task result")

    @pytest.mark.asyncio
    async def test_suppress_response_skips_discord_delivery(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        agent_client.extract_text = MagicMock(return_value="<SUPPRESS RESPONSE>\nquiet.")

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        discord_sender.send_channel.assert_not_awaited()
        discord_sender.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_channel_tag_routes_to_specific_channel(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        agent_client.extract_text = MagicMock(
            return_value="<CHANNEL:999>Email summary.</CHANNEL>"
        )

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        discord_sender.send_channel.assert_awaited_once_with("999", "Email summary.")
        discord_sender.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_channel_tag_plus_remainder_routes_both(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        agent_client.extract_text = MagicMock(
            return_value="<CHANNEL:777>Routed bit.</CHANNEL>\nFallback text."
        )

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        discord_sender.send_channel.assert_any_await("777", "Routed bit.")
        # remainder goes to the task's discord_channel_id ("c1")
        discord_sender.send_channel.assert_any_await("c1", "Fallback text.")

    @pytest.mark.asyncio
    async def test_offline_task_does_not_auto_decline_confirmation_requests(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        agent_client.extract_confirmation_requests.assert_not_called()
        agent_client.send_confirmation_responses_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_schedule_next_creates_recurring_followup_task(
        self, executor, firestore_client, agent_client, discord_sender
    ):
        next_run = datetime.now(timezone.utc) + timedelta(days=7)
        agent_client.extract_text = MagicMock(
            return_value=(
                "Added 3 restaurants.\n"
                f"SCHEDULE_NEXT: every week | {next_run.isoformat()}"
            )
        )
        with patch.object(
            executor,
            "_compute_cloud_task_name",
            return_value="projects/p/tasks/followup",
        ), patch.object(
            executor,
            "_create_cloud_task",
            return_value="projects/p/tasks/followup",
        ):
            result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.set.assert_awaited_once()
        followup_doc = doc_ref.set.await_args.args[0]
        assert followup_doc["status"] == "scheduled"
        assert followup_doc["task_type"] == "research"
        assert followup_doc["instruction"] == "Research AI"
        assert followup_doc["context"] == {"topic": "AI"}
        assert followup_doc["allowed_resource_ids"] == ["sheet-1", "doc-1"]
        assert followup_doc["recurrence_rule"] == "every week"
        assert followup_doc["parent_task_id"] == TASK_ID
        assert followup_doc["discord_channel_id"] == "c1"
        assert followup_doc["scheduled_at"].isoformat() == next_run.isoformat()
        assert followup_doc["cloud_task_name"] == "projects/p/tasks/followup"

    @pytest.mark.asyncio
    async def test_invalid_schedule_next_is_ignored(
        self, executor, firestore_client, agent_client
    ):
        agent_client.extract_text = MagicMock(
            return_value="Done.\nSCHEDULE_NEXT: every week | not-a-date"
        )

        result = await executor.execute_task(TASK_ID)

        assert result["success"] is True
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.set.assert_not_awaited()


ENV = {
    "GOOGLE_CLOUD_PROJECT": "test-project",
    "GOOGLE_CLOUD_LOCATION": "us-central1",
    "ASYNC_TASKS_QUEUE": "async-agent-tasks",
    "SMS_GATEWAY_URL": "https://gateway.example.com",
    "SMS_GATEWAY_SA": "svc@test-project.iam.gserviceaccount.com",
}


class TestComputeCloudTaskName:
    def test_uuid_normalized_to_lowercase_hyphens(self, executor):
        with patch.dict("os.environ", ENV):
            name = executor._compute_cloud_task_name("abc-123-XYZ")
        assert name == (
            "projects/test-project/locations/us-central1"
            "/queues/async-agent-tasks/tasks/execute-abc-123-xyz-initial"
        )

    def test_underscores_replaced_with_hyphens(self, executor):
        with patch.dict("os.environ", ENV):
            name = executor._compute_cloud_task_name("task_with_underscores")
        assert "task-with-underscores" in name

    def test_missing_project_returns_none(self, executor):
        with patch.dict("os.environ", {}, clear=True):
            assert executor._compute_cloud_task_name("some-id") is None


class TestCreateCloudTask:
    def _make_tasks_client(self):
        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-central1/queues/async-agent-tasks"
        client.task_path.return_value = "projects/test-project/locations/us-central1/queues/async-agent-tasks/tasks/execute-task-123-initial"
        client.create_task.return_value.name = "projects/test-project/locations/us-central1/queues/async-agent-tasks/tasks/execute-task-123-initial"
        return client

    def test_creates_task_and_returns_name(self, executor):
        tasks_client = self._make_tasks_client()
        with patch.dict("os.environ", ENV), \
             patch("google.cloud.tasks_v2.CloudTasksClient", return_value=tasks_client):
            result = executor._create_cloud_task(
                task_id="task-123",
                user_id="user-1",
                schedule_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            )
        assert result == tasks_client.create_task.return_value.name
        tasks_client.create_task.assert_called_once()
        call_kwargs = tasks_client.create_task.call_args.kwargs
        assert call_kwargs["parent"] == tasks_client.queue_path.return_value

    def test_already_exists_returns_task_name(self, executor):
        from google.api_core.exceptions import AlreadyExists

        tasks_client = self._make_tasks_client()
        tasks_client.create_task.side_effect = AlreadyExists("duplicate")
        with patch.dict("os.environ", ENV), \
             patch("google.cloud.tasks_v2.CloudTasksClient", return_value=tasks_client):
            result = executor._create_cloud_task(
                task_id="task-123",
                user_id="user-1",
                schedule_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            )
        assert result == tasks_client.task_path.return_value

    def test_unexpected_exception_returns_none(self, executor):
        tasks_client = self._make_tasks_client()
        tasks_client.create_task.side_effect = RuntimeError("transient error")
        with patch.dict("os.environ", ENV), \
             patch("google.cloud.tasks_v2.CloudTasksClient", return_value=tasks_client):
            result = executor._create_cloud_task(
                task_id="task-123",
                user_id="user-1",
                schedule_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            )
        assert result is None

    def test_missing_env_vars_returns_none(self, executor):
        with patch.dict("os.environ", {}, clear=True):
            result = executor._create_cloud_task(
                task_id="task-123",
                user_id="user-1",
                schedule_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            )
        assert result is None

    def test_naive_schedule_time_gets_utc_timezone(self, executor):
        tasks_client = self._make_tasks_client()
        with patch.dict("os.environ", ENV), \
             patch("google.cloud.tasks_v2.CloudTasksClient", return_value=tasks_client):
            executor._create_cloud_task(
                task_id="task-123",
                user_id="user-1",
                schedule_time=datetime(2026, 6, 1, 12, 0, 0),  # naive
            )
        tasks_client.create_task.assert_called_once()


class TestRequeueScheduledTasks:
    def _make_scheduled_doc(self, task_id: str = "task-sched-1", **overrides) -> MagicMock:
        data = {
            "task_id": task_id,
            "user_id": USER_ID,
            "status": "scheduled",
            "scheduled_at": datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        }
        data.update(overrides)
        doc = MagicMock()
        doc.to_dict.return_value = data
        return doc

    def _set_query_stream(self, firestore_client, docs):
        async def _stream():
            for doc in docs:
                yield doc

        query = MagicMock()
        query.stream.return_value = _stream()
        firestore_client.collection.return_value.where.return_value.where.return_value = query
        return query

    @pytest.mark.asyncio
    async def test_requeue_queues_tasks_in_window(self, executor, firestore_client):
        doc = self._make_scheduled_doc()
        self._set_query_stream(firestore_client, [doc])
        doc_ref = firestore_client.collection.return_value.document.return_value
        expected_name = "projects/p/queues/q/tasks/execute-task-sched-1-initial"

        with patch.object(executor, "_compute_cloud_task_name", return_value=expected_name), \
             patch.object(executor, "_create_cloud_task", return_value=expected_name):
            result = await executor.requeue_scheduled_tasks()

        assert result == {"queued": 1, "errors": 0}
        doc_ref.update.assert_any_await({"cloud_task_name": expected_name})

    @pytest.mark.asyncio
    async def test_requeue_skips_tasks_already_queued(self, executor, firestore_client):
        doc = self._make_scheduled_doc(cloud_task_name="projects/p/queues/q/tasks/existing")
        self._set_query_stream(firestore_client, [doc])

        with patch.object(executor, "_create_cloud_task") as mock_create:
            result = await executor.requeue_scheduled_tasks()

        assert result == {"queued": 0, "errors": 0}
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_requeue_skips_tasks_outside_window(self, executor, firestore_client):
        # Firestore query filters outside-window tasks; simulate empty result set.
        self._set_query_stream(firestore_client, [])

        with patch.object(executor, "_create_cloud_task") as mock_create:
            result = await executor.requeue_scheduled_tasks()

        assert result == {"queued": 0, "errors": 0}
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_requeue_skips_task_with_no_scheduled_at(self, executor, firestore_client):
        doc = self._make_scheduled_doc(scheduled_at=None)
        self._set_query_stream(firestore_client, [doc])

        with patch.object(executor, "_create_cloud_task") as mock_create:
            result = await executor.requeue_scheduled_tasks()

        assert result == {"queued": 0, "errors": 1}
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_requeue_rolls_back_name_on_cloud_task_failure(self, executor, firestore_client):
        doc = self._make_scheduled_doc()
        self._set_query_stream(firestore_client, [doc])
        doc_ref = firestore_client.collection.return_value.document.return_value
        expected_name = "projects/p/queues/q/tasks/execute-task-sched-1-initial"

        with patch.object(executor, "_compute_cloud_task_name", return_value=expected_name), \
             patch.object(executor, "_create_cloud_task", return_value=None):
            result = await executor.requeue_scheduled_tasks()

        assert result == {"queued": 0, "errors": 1}
        # Name written first, then rolled back with DELETE_FIELD on failure.
        update_calls = [c.args[0] for c in doc_ref.update.await_args_list]
        assert {"cloud_task_name": expected_name} in update_calls
        from google.cloud import firestore as fs_module
        rollback_call = [c for c in update_calls if "cloud_task_name" in c and c["cloud_task_name"] != expected_name]
        assert len(rollback_call) == 1
        assert rollback_call[0]["cloud_task_name"] is fs_module.DELETE_FIELD


class TestCreateEmailBatchTask:
    @pytest.mark.asyncio
    async def test_creates_firestore_doc_and_cloud_task(self, executor, firestore_client):
        tasks_client = MagicMock()
        tasks_client.queue_path.return_value = "projects/test-project/locations/us-central1/queues/async-agent-tasks"
        tasks_client.task_path.return_value = "projects/test-project/locations/us-central1/queues/async-agent-tasks/tasks/execute-abc-initial"
        tasks_client.create_task.return_value.name = "projects/test-project/locations/us-central1/queues/async-agent-tasks/tasks/execute-abc-initial"

        scheduled = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
        with patch.dict("os.environ", ENV), \
             patch("google.cloud.tasks_v2.CloudTasksClient", return_value=tasks_client):
            task_id = await executor.create_email_batch_task(
                gmail_address="mirko@example.com",
                user_id="user-1",
                prompt="Process emails",
                discord_channel_id="ch-1",
                scheduled_at=scheduled,
            )

        assert task_id  # UUID returned
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.set.assert_awaited_once()
        set_doc = doc_ref.set.await_args.args[0]
        assert set_doc["task_type"] == "notification"
        assert set_doc["status"] == "scheduled"
        assert set_doc["gmail_address"] == "mirko@example.com"
        assert set_doc["discord_channel_id"] == "ch-1"
        doc_ref.update.assert_awaited_once_with(
            {"cloud_task_name": tasks_client.create_task.return_value.name}
        )

    @pytest.mark.asyncio
    async def test_logs_warning_when_cloud_task_creation_fails(self, executor, firestore_client):
        scheduled = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
        with patch.dict("os.environ", {}, clear=True):  # missing env → _create_cloud_task returns None
            task_id = await executor.create_email_batch_task(
                gmail_address="mirko@example.com",
                user_id="user-1",
                prompt="Process emails",
                discord_channel_id="ch-1",
                scheduled_at=scheduled,
            )

        assert task_id
        doc_ref = firestore_client.collection.return_value.document.return_value
        doc_ref.set.assert_awaited_once()
        doc_ref.update.assert_not_awaited()  # no cloud_task_name update when creation failed
