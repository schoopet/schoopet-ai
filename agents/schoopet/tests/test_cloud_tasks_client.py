"""Unit tests for CloudTasksClient."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.schoopet.tools.cloud_tasks_client import CloudTasksClient


class _FakeTimestamp:
    def __init__(self):
        self.value = None

    def FromDatetime(self, value):
        self.value = value


class _FakeDuration:
    def __init__(self, seconds=0):
        self.seconds = seconds


class _AlreadyExists(Exception):
    """Fake AlreadyExists exception."""


def _patch_google_modules():
    return patch.dict(
        "sys.modules",
        {
            "google.cloud.tasks_v2": SimpleNamespace(
                HttpMethod=SimpleNamespace(POST="POST")
            ),
            "google.protobuf.timestamp_pb2": SimpleNamespace(Timestamp=_FakeTimestamp),
            "google.protobuf.duration_pb2": SimpleNamespace(Duration=_FakeDuration),
            "google.api_core.exceptions": SimpleNamespace(AlreadyExists=_AlreadyExists),
        },
    )


def _make_client(mock_client: MagicMock) -> CloudTasksClient:
    client = CloudTasksClient()
    with patch.dict(
        "os.environ",
        {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "ASYNC_TASKS_QUEUE": "async-agent-tasks",
            "SMS_GATEWAY_URL": "https://gateway.example.com",
            "SMS_GATEWAY_SA": "schoopet-sms-gateway@test-project.iam.gserviceaccount.com",
        },
    ):
        client._ensure_initialized()
        client._client = mock_client
    return client


class TestCloudTasksClient:
    def test_create_task_sets_deterministic_name_and_deadline(self):
        mock_client = MagicMock()
        mock_client.task_path.return_value = (
            "projects/test-project/locations/us-central1/queues/async-agent-tasks/"
            "tasks/execute-task-123-initial"
        )
        mock_client.create_task.return_value = SimpleNamespace(name=mock_client.task_path.return_value)
        client = _make_client(mock_client)

        with _patch_google_modules():
            schedule_time = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
            result = client.create_task(
                task_id="task-123",
                user_id="user-123",
                schedule_time=schedule_time,
            )

        assert result == mock_client.task_path.return_value
        task = mock_client.create_task.call_args.kwargs["task"]
        assert task["name"] == mock_client.task_path.return_value
        assert task["http_request"]["url"] == "https://gateway.example.com/internal/tasks/execute"
        assert task["http_request"]["oidc_token"]["audience"] == "https://gateway.example.com"
        assert (
            task["http_request"]["oidc_token"]["service_account_email"]
            == "schoopet-sms-gateway@test-project.iam.gserviceaccount.com"
        )
        assert task["http_request"]["body"] == b'{"task_id": "task-123", "user_id": "user-123"}'
        assert task["dispatch_deadline"].seconds == 900
        if hasattr(task["schedule_time"], "value"):
            assert task["schedule_time"].value == schedule_time
        else:
            assert task["schedule_time"].ToDatetime().replace(tzinfo=timezone.utc) == schedule_time

    def test_create_task_reuses_name_on_already_exists(self):
        mock_client = MagicMock()
        mock_client.task_path.return_value = (
            "projects/test-project/locations/us-central1/queues/async-agent-tasks/"
            "tasks/execute-task-123-initial"
        )
        mock_client.create_task.side_effect = _AlreadyExists("duplicate task")
        client = _make_client(mock_client)

        with _patch_google_modules():
            result = client.create_task(
                task_id="task-123",
                user_id="user-123",
            )

        assert result == mock_client.task_path.return_value
