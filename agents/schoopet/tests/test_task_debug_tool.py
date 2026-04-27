"""Unit tests for TaskDebugTool."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agents.schoopet.task_debug_tool import TaskDebugTool


TASK_ID = "task-123"
USER_ID = "+14155551234"
CLOUD_TASK_NAME = "projects/test/locations/us-central1/queues/q/tasks/execute-task-123-initial"


def _make_firestore_doc(data):
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = data
    return doc


def test_get_cloud_task_status_uses_firestore_task(tool_context):
    """Should read Cloud Task metadata using the Firestore task record."""
    tool = TaskDebugTool()
    mock_firestore = MagicMock()
    tool._firestore_client = mock_firestore
    mock_firestore.collection.return_value.document.return_value.get.return_value = _make_firestore_doc(
        {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "find stuff",
            "status": "scheduled",
            "created_at": datetime.now(timezone.utc),
            "scheduled_at": datetime.now(timezone.utc),
            "cloud_task_name": CLOUD_TASK_NAME,
        }
    )

    with patch("agents.schoopet.task_debug_tool.get_cloud_tasks_client") as mock_get_client:
        mock_get_client.return_value.get_task_status.return_value = {
            "name": CLOUD_TASK_NAME,
            "schedule_time": datetime(2026, 4, 27, 17, 0, tzinfo=timezone.utc),
            "create_time": datetime(2026, 4, 27, 16, 0, tzinfo=timezone.utc),
            "dispatch_count": 0,
            "response_count": 0,
            "last_attempt": None,
            "first_attempt": None,
        }
        result = tool.get_cloud_task_status(task_id=TASK_ID, tool_context=tool_context)

    assert CLOUD_TASK_NAME in result
    assert "Firestore status: scheduled" in result


def test_list_scheduled_tasks_formats_tasks(tool_context):
    """Should format scheduled task summaries with queue metadata."""
    tool = TaskDebugTool()
    mock_firestore = MagicMock()
    tool._firestore_client = mock_firestore
    mock_firestore.collection.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value.get.return_value = [
        _make_firestore_doc(
            {
                "task_id": TASK_ID,
                "user_id": USER_ID,
                "task_type": "research",
                "instruction": "find stuff",
                "status": "scheduled",
                "created_at": datetime.now(timezone.utc),
                "scheduled_at": datetime(2026, 4, 27, 17, 0, tzinfo=timezone.utc),
                "cloud_task_name": CLOUD_TASK_NAME,
            }
        )
    ]

    with patch("agents.schoopet.task_debug_tool.get_cloud_tasks_client") as mock_get_client:
        mock_get_client.return_value.get_task_status.return_value = {
            "name": CLOUD_TASK_NAME,
            "schedule_time": datetime(2026, 4, 27, 17, 0, tzinfo=timezone.utc),
            "create_time": datetime(2026, 4, 27, 16, 0, tzinfo=timezone.utc),
            "dispatch_count": 0,
            "response_count": 0,
            "last_attempt": None,
        }
        result = tool.list_scheduled_tasks(tool_context=tool_context)

    assert "Scheduled tasks:" in result
    assert TASK_ID in result
    assert "Queue status:" in result


def test_debug_task_includes_cloud_metadata(tool_context):
    """Should build a combined Firestore and Cloud Tasks report."""
    tool = TaskDebugTool()
    mock_firestore = MagicMock()
    tool._firestore_client = mock_firestore
    mock_firestore.collection.return_value.document.return_value.get.return_value = _make_firestore_doc(
        {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "task_type": "research",
            "instruction": "find stuff",
            "status": "scheduled",
            "created_at": datetime.now(timezone.utc),
            "scheduled_at": datetime(2026, 4, 27, 17, 0, tzinfo=timezone.utc),
            "cloud_task_name": CLOUD_TASK_NAME,
        }
    )

    with patch("agents.schoopet.task_debug_tool.get_cloud_tasks_client") as mock_get_client:
        mock_get_client.return_value.get_task_status.return_value = {
            "name": CLOUD_TASK_NAME,
            "schedule_time": datetime(2026, 4, 27, 17, 0, tzinfo=timezone.utc),
            "create_time": datetime(2026, 4, 27, 16, 0, tzinfo=timezone.utc),
            "dispatch_count": 1,
            "response_count": 1,
            "last_attempt": {
                "dispatch_time": datetime(2026, 4, 27, 17, 0, tzinfo=timezone.utc),
                "response_time": datetime(2026, 4, 27, 17, 0, 5, tzinfo=timezone.utc),
                "http_status_code": 200,
                "http_status_message": "OK",
            },
        }
        result = tool.debug_task(TASK_ID, tool_context=tool_context)

    assert "Cloud Task name" in result
    assert "Dispatch count: 1" in result
    assert "Last attempt HTTP status: 200 OK" in result
