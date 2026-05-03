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
            allowed_resource_ids={
                "sheet": ["sheet-abc", "sheet-def"],
                "doc": ["doc-xyz"],
                "drive_folder": ["folder-1"],
            },
            tool_context=tool_context,
        )

        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args["allowed_resource_ids"] == {
            "sheet": ["sheet-abc", "sheet-def"],
            "doc": ["doc-xyz"],
            "drive_folder": ["folder-1"],
        }

    def test_create_async_task_no_allowed_resources_stores_empty(self, async_task_tool, mock_cloud_tasks, tool_context, mock_firestore):
        """Should store empty allowed_resource_ids when none are passed."""
        mock_cloud_tasks.create_task.return_value = "projects/p/locations/l/queues/q/tasks/t1"

        async_task_tool.create_async_task(
            task_type="research",
            instruction="Research AI",
            tool_context=tool_context,
        )

        call_args = mock_firestore.collection.return_value.document.return_value.set.call_args[0][0]
        assert call_args.get("allowed_resource_ids") == {}

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

    def test_check_task_status_found(self, async_task_tool, tool_context, mock_firestore):
        """Should return task status."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "Research AI",
            "status": "approved",
            "result": "AI is cool",
            "created_at": datetime.now(timezone.utc),
            "agent_type": "personal"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.check_task_status(TASK_ID, tool_context=tool_context)

        assert "Status: approved" in result
        assert "Result preview: AI is cool" in result

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
            "agent_type": "personal"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.cancel_task(TASK_ID, tool_context=tool_context)

        assert f"Task {TASK_ID} cancelled" in result
        mock_cloud_tasks.cancel_task.assert_called_with("task-name")
        mock_firestore.collection.return_value.document.return_value.update.assert_called_with({
            "status": "cancelled",
            "completed_at": ANY
        })

    def test_review_task_result(self, async_task_tool, tool_context, mock_firestore):
        """Should show review details for supervisor."""
        # Supervisor ID
        supervisor_context = MagicMock(spec=ToolContext)
        supervisor_context.user_id = f"{USER_ID}_supervisor"

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "Research AI",
            "status": "awaiting_review",
            "result": "Draft Result",
            "created_at": datetime.now(timezone.utc),
            "agent_type": "personal"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.review_task_result(TASK_ID, tool_context=supervisor_context)

        assert "=== Task Review: research ===" in result
        assert "Draft Result" in result
        assert "Use approve_task" in result

    def test_approve_task(self, async_task_tool, tool_context, mock_firestore, mock_cloud_tasks):
        """Should approve task and trigger notification Cloud Task."""
        supervisor_context = MagicMock(spec=ToolContext)
        supervisor_context.user_id = f"{USER_ID}_supervisor"

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "awaiting_review",
            "result": "Final Result",
            "created_at": datetime.now(timezone.utc),
            "instruction": "Test",
            "task_type": "research",
            "agent_type": "personal"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = async_task_tool.approve_task(TASK_ID, tool_context=supervisor_context)

        assert "approved" in result
        mock_firestore.collection.return_value.document.return_value.update.assert_any_call({
            "status": "approved",
            "reviewed_at": ANY
        })
        mock_cloud_tasks.create_notification_task.assert_called_once()

    def test_approve_task_does_not_set_notified(self, async_task_tool, tool_context, mock_firestore, mock_cloud_tasks):
        """approve_task must NOT set status=notified — that is the gateway's job
        after confirmed delivery. Setting it early was the status race condition."""
        supervisor_context = MagicMock(spec=ToolContext)
        supervisor_context.user_id = f"{USER_ID}_supervisor"

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "awaiting_review",
            "result": "Final Result",
            "created_at": datetime.now(timezone.utc),
            "instruction": "Test",
            "task_type": "research",
            "agent_type": "personal",
            "notification_channel": "sms",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        async_task_tool.approve_task(TASK_ID, tool_context=supervisor_context)

        all_updates = mock_firestore.collection.return_value.document.return_value.update.call_args_list
        statuses_written = [
            call[0][0].get("status")
            for call in all_updates
            if isinstance(call[0][0], dict) and "status" in call[0][0]
        ]
        assert "notified" not in statuses_written, (
            f"approve_task must not write status=notified (got: {statuses_written}). "
            "NOTIFIED is set by /internal/user-notify after confirmed delivery."
        )

    def test_request_correction(self, async_task_tool, tool_context, mock_firestore, mock_cloud_tasks):
        """Should request revision."""
        supervisor_context = MagicMock(spec=ToolContext)
        supervisor_context.user_id = f"{USER_ID}_supervisor"

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "awaiting_review",
            "result": "Bad Result",
            "review_attempts": 0,
            "max_review_attempts": 3,
            "created_at": datetime.now(timezone.utc),
            "instruction": "Test",
            "task_type": "research",
            "agent_type": "personal"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_cloud_tasks.create_revision_task.return_value = "revision-task"

        result = async_task_tool.request_correction(
            TASK_ID, 
            feedback="Make it better", 
            tool_context=supervisor_context
        )

        assert "Revision requested" in result
        mock_firestore.collection.return_value.document.return_value.update.assert_any_call({
            "status": "revision_requested",
            "revision_feedback": "Make it better",
            "review_attempts": 1
        })
        mock_cloud_tasks.create_revision_task.assert_called_once_with(
            task_id=TASK_ID,
            user_id=USER_ID,
            revision_number=1,
        )
        mock_cloud_tasks.create_revision_task.assert_called_once()
