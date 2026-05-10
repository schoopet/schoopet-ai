"""Unit tests for AsyncTaskTool."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, ANY
from agents.schoopet.tools.async_task_tool import AsyncTaskTool, AsyncTaskDocument, TaskStatus
from google.adk.tools import ToolContext

# Sample data
USER_ID = "user123"
TASK_ID = "task-uuid-123"
SESSION_ID = "session-456"

@pytest.fixture
def tool_context():
    """Create a mock ToolContext."""
    context = MagicMock(spec=ToolContext)
    context.user_id = USER_ID
    context.session_id = SESSION_ID
    context.state = {}
    return context

@pytest.fixture
def mock_firestore():
    """Mock Firestore client."""
    mock_module = MagicMock()
    client = MagicMock()
    mock_module.Client.return_value = client
    
    with patch.dict("sys.modules", {"google.cloud.firestore": mock_module}):
        yield client

@pytest.fixture
def mock_cloud_tasks():
    """Mock Cloud Tasks client."""
    with patch("agents.schoopet.tools.async_task_tool.get_cloud_tasks_client") as mock_ct:
        client = MagicMock()
        mock_ct.return_value = client
        yield client

@pytest.fixture
def async_task_tool(mock_firestore):
    """Create AsyncTaskTool instance."""
    tool = AsyncTaskTool()
    # Force initialization with mock project
    with patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
        tool._ensure_initialized()
        tool._firestore_client = mock_firestore
    return tool

class TestAsyncTaskTool:
    """Tests for AsyncTaskTool class."""

    def test_create_async_task_success(self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore):
        """Should create a task document and cloud task."""
        mock_cloud_tasks.create_task.return_value = "projects/p/locations/l/queues/q/tasks/t1"
        
        result = async_task_tool.create_async_task(
            task_type="research",
            instruction="Research AI",
            tool_context=tool_context
        )

        assert "Started async research task" in result
        assert "Task ID:" in result

        # Verify Firestore set
        mock_firestore.collection.return_value.document.return_value.set.assert_called_once()
        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["task_type"] == "research"
        assert call_args["user_id"] == USER_ID
        assert call_args["status"] == "pending"

        # Verify Cloud Task creation
        mock_cloud_tasks.create_task.assert_called_once()

    def test_create_async_task_with_allowed_resources(self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore):
        """Should store allowed_resource_ids in the Firestore document."""
        mock_cloud_tasks.create_task.return_value = "projects/p/locations/l/queues/q/tasks/t1"

        async_task_tool.create_async_task(
            task_type="research",
            instruction="DEEP_RESEARCH_TASK: find restaurants",
            allowed_resource_ids=["sheet-abc", "sheet-def", "doc-xyz", "folder-1"],
            tool_context=tool_context,
        )

        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["allowed_resource_ids"] == ["sheet-abc", "sheet-def", "doc-xyz", "folder-1"]

    def test_create_async_task_no_allowed_resources_stores_empty(self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore):
        """Should store empty allowed_resource_ids when none are passed."""
        mock_cloud_tasks.create_task.return_value = "projects/p/locations/l/queues/q/tasks/t1"

        async_task_tool.create_async_task(
            task_type="research",
            instruction="Research AI",
            tool_context=tool_context,
        )

        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args.get("allowed_resource_ids") == []

    def test_create_async_task_stores_discord_channel_notification_context(
        self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore
    ):
        """Discord channel sessions should notify back to the originating channel."""
        mock_cloud_tasks.create_task.return_value = "projects/p/locations/l/queues/q/tasks/t1"
        tool_context.state = {
            "channel": "discord",
            "session_scope": "discord:guild:g1:channel:c1",
            "discord_channel_id": "c1",
            "discord_channel_name": "project-alpha",
        }

        async_task_tool.create_async_task(
            task_type="research",
            instruction="Research launch risks",
            tool_context=tool_context,
        )

        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["notification_channel"] == "discord"
        assert call_args["notification_session_scope"] == "discord:guild:g1:channel:c1"
        assert call_args["notification_target_type"] == "discord_channel"
        assert call_args["discord_channel_id"] == "c1"
        assert call_args["discord_channel_name"] == "project-alpha"

    def test_create_async_task_requires_discord_channel_id(
        self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore
    ):
        """Discord tasks must have a concrete channel target for completion delivery."""
        tool_context.state = {
            "channel": "discord",
            "session_scope": "discord:guild:g1:channel:c1",
        }

        result = async_task_tool.create_async_task(
            task_type="research",
            instruction="Research launch risks",
            tool_context=tool_context,
        )

        assert result.startswith("ERROR: Cannot create async task")
        mock_firestore.collection.return_value.document.return_value.set.assert_not_called()
        mock_cloud_tasks.create_task.assert_not_called()

    def test_create_async_task_scheduled(self, async_task_tool, mock_cloud_tasks, tool_context):
        """Should create a scheduled task."""
        mock_cloud_tasks.create_task.return_value = "task-name"
        
        schedule_time = "2025-01-01T12:00:00Z"
        
        result = async_task_tool.create_async_task(
            task_type="reminder",
            instruction="Call mom",
            schedule_at=schedule_time,
            tool_context=tool_context
        )

        assert "Scheduled reminder task" in result
        
        # Verify Cloud Task scheduled time
        _, kwargs = mock_cloud_tasks.create_task.call_args
        assert kwargs["schedule_time"].isoformat() == "2025-01-01T12:00:00+00:00"

    def test_create_async_task_naive_schedule_uses_saved_timezone(
        self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore
    ):
        """Naive schedule_at values should be interpreted in the user's timezone."""
        mock_cloud_tasks.create_task.return_value = "task-name"
        async_task_tool._preferences_tool.get_timezone_value = MagicMock(
            return_value="America/Los_Angeles"
        )

        result = async_task_tool.create_async_task(
            task_type="reminder",
            instruction="Call mom",
            schedule_at="2025-01-01T12:00:00",
            tool_context=tool_context,
        )

        assert "Scheduled reminder task" in result
        _, kwargs = mock_cloud_tasks.create_task.call_args
        assert kwargs["schedule_time"].isoformat() == "2025-01-01T12:00:00-08:00"
        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["scheduled_at"].isoformat() == "2025-01-01T12:00:00-08:00"
        async_task_tool._preferences_tool.get_timezone_value.assert_called_once_with(USER_ID)

    def test_create_async_task_aware_schedule_preserves_explicit_timezone(
        self, async_task_tool, mock_cloud_tasks, tool_context
    ):
        """Explicit timezone offsets should not be reinterpreted via user preference."""
        mock_cloud_tasks.create_task.return_value = "task-name"
        async_task_tool._preferences_tool.get_timezone_value = MagicMock(
            return_value="America/Los_Angeles"
        )

        async_task_tool.create_async_task(
            task_type="reminder",
            instruction="Call mom",
            schedule_at="2025-01-01T12:00:00Z",
            tool_context=tool_context,
        )

        _, kwargs = mock_cloud_tasks.create_task.call_args
        assert kwargs["schedule_time"].isoformat() == "2025-01-01T12:00:00+00:00"
        async_task_tool._preferences_tool.get_timezone_value.assert_not_called()

    def test_create_async_task_naive_schedule_falls_back_to_utc_without_timezone(
        self, async_task_tool, mock_cloud_tasks, tool_context
    ):
        """Naive schedule_at values should remain schedulable when no preference exists."""
        mock_cloud_tasks.create_task.return_value = "task-name"
        async_task_tool._preferences_tool.get_timezone_value = MagicMock(return_value=None)

        async_task_tool.create_async_task(
            task_type="reminder",
            instruction="Call mom",
            schedule_at="2025-01-01T12:00:00",
            tool_context=tool_context,
        )

        _, kwargs = mock_cloud_tasks.create_task.call_args
        assert kwargs["schedule_time"].isoformat() == "2025-01-01T12:00:00+00:00"

    def test_check_task_status_found(self, async_task_tool, tool_context, mock_firestore):
        """Should return task status."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "Research AI",
            "status": "completed",
            "result": "AI is cool",
            "created_at": datetime.now(timezone.utc),
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.check_task_status(TASK_ID, tool_context=tool_context)

        assert "Status: completed" in result
        assert "Result preview: AI is cool" in result

    def test_get_task_result_completed_for_owner(self, async_task_tool, tool_context, mock_firestore):
        """Should return the stored task result for the owning user."""
        completed_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "Research AI",
            "status": "notified",
            "result": "Full research result",
            "created_at": datetime.now(timezone.utc),
            "completed_at": completed_at,
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.get_task_result(TASK_ID, tool_context=tool_context)

        assert f"task_id: {TASK_ID}" in result
        assert "task_type: research" in result
        assert "status: notified" in result
        assert f"completed_at: {completed_at.isoformat()}" in result
        assert "truncated: false" in result
        assert "Full research result" in result

    def test_get_task_result_truncates_to_requested_limit(self, async_task_tool, tool_context, mock_firestore):
        """Should truncate long stored results and mark truncated=true."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "Research AI",
            "status": "completed",
            "result": "abcdef",
            "created_at": datetime.now(timezone.utc),
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.get_task_result(
            TASK_ID,
            max_chars=3,
            tool_context=tool_context,
        )

        assert "truncated: true" in result
        assert result.endswith("abc")
        assert "abcdef" not in result

    def test_get_task_result_wrong_user_not_found(self, async_task_tool, tool_context, mock_firestore):
        """Should not expose task results across users."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": "other-user",
            "task_type": "research",
            "instruction": "Research AI",
            "status": "completed",
            "result": "Secret result",
            "created_at": datetime.now(timezone.utc),
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.get_task_result(TASK_ID, tool_context=tool_context)

        assert result == f"Task {TASK_ID} not found."

    def test_get_task_result_pending_has_no_result(self, async_task_tool, tool_context, mock_firestore):
        """Should return status metadata when result is not ready yet."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "analysis",
            "instruction": "Analyze calendar",
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.get_task_result(TASK_ID, tool_context=tool_context)

        assert f"task_id: {TASK_ID}" in result
        assert "task_type: analysis" in result
        assert "status: pending" in result
        assert "truncated: false" in result
        assert "result: Result not available yet." in result

    def test_get_task_result_failed_returns_error(self, async_task_tool, tool_context, mock_firestore):
        """Should return the stored error for failed tasks."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "Research AI",
            "status": "failed",
            "error": "Worker timed out",
            "created_at": datetime.now(timezone.utc),
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.get_task_result(TASK_ID, tool_context=tool_context)

        assert "status: failed" in result
        assert "truncated: false" in result
        assert "error: Worker timed out" in result

    def test_cancel_task_success(self, async_task_tool, tool_context, mock_firestore, mock_cloud_tasks):
        """Should cancel pending task."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "cloud_task_name": "task-name",
            "created_at": datetime.now(timezone.utc),
            "instruction": "Test",
            "task_type": "research",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.cancel_task(TASK_ID, tool_context=tool_context)

        assert f"Task {TASK_ID} cancelled" in result
        mock_cloud_tasks.cancel_task.assert_called_with("task-name")
        mock_firestore.collection.return_value.document.return_value.update.assert_called_with({
            "status": "cancelled",
            "completed_at": ANY
        })
