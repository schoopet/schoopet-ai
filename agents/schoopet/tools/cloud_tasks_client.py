"""Cloud Tasks client for scheduling async agent tasks.

This module provides integration with Google Cloud Tasks for:
- Immediate async task execution
- Scheduled task execution (reminders, future tasks)
- Task cancellation

Security: All tasks use OIDC authentication for secure service-to-service calls.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _timestamp_to_datetime(value):
    """Convert a protobuf timestamp-like object to datetime when present."""
    if not value:
        return None

    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        return to_datetime()

    return None


class CloudTasksClient:
    """Client for creating and managing Cloud Tasks for async agent execution.

    Uses lazy initialization to avoid issues during agent pickling/deployment.
    """

    def __init__(self):
        """Initialize the Cloud Tasks Client - all initialization is deferred."""
        self._client = None
        self._initialized = False

        # Configuration (loaded lazily)
        self._project_id = None
        self._location = None
        self._queue_name = None
        self._gateway_url = None
        self._service_account = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration from environment variables."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self._queue_name = os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks")
        self._gateway_url = os.getenv("SMS_GATEWAY_URL")
        self._service_account = os.getenv(
            "SMS_GATEWAY_SA",
            f"schoopet-sms-gateway@{self._project_id}.iam.gserviceaccount.com"
            if self._project_id
            else None,
        )

        self._initialized = True

        if not self._project_id:
            logger.warning("GOOGLE_CLOUD_PROJECT not set, Cloud Tasks will not work")
        if not self._gateway_url:
            logger.warning("SMS_GATEWAY_URL not set, Cloud Tasks will not work")

    def _get_client(self):
        """Get Cloud Tasks client, initializing lazily."""
        if self._client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import tasks_v2
                self._client = tasks_v2.CloudTasksClient(transport="rest")
        return self._client

    @property
    def queue_path(self) -> Optional[str]:
        """Get the full queue path."""
        self._ensure_initialized()
        client = self._get_client()
        if not client or not self._project_id:
            return None
        return client.queue_path(self._project_id, self._location, self._queue_name)

    def _build_task_name(self, prefix: str, suffix: str) -> Optional[str]:
        """Build a deterministic Cloud Tasks resource name."""
        client = self._get_client()
        if not client or not self._project_id:
            return None

        normalized_suffix = re.sub(r"[^a-zA-Z0-9-]", "-", suffix).strip("-").lower()
        task_id = f"{prefix}-{normalized_suffix}"[:500]
        return client.task_path(
            self._project_id,
            self._location,
            self._queue_name,
            task_id,
        )

    def create_task(
        self,
        task_id: str,
        user_id: str,
        schedule_time: Optional[datetime] = None,
    ) -> Optional[str]:
        """Create a Cloud Task to execute an async task.

        Args:
            task_id: The Firestore task document ID (UUID)
            user_id: User identifier
            schedule_time: When to execute (None = immediate)

        Returns:
            Cloud Task name if successful, None otherwise
        """
        self._ensure_initialized()
        client = self._get_client()

        if not client or not self._gateway_url:
            logger.error("Cloud Tasks client not initialized or gateway URL not set")
            return None

        # Import here to avoid issues during pickling
        from google.cloud import tasks_v2
        from google.api_core.exceptions import AlreadyExists
        from google.protobuf import duration_pb2
        from google.protobuf import timestamp_pb2

        # Build the task payload
        payload = {"task_id": task_id, "user_id": user_id}
        task_name = self._build_task_name("execute", f"{task_id}-initial")

        task = {
            "name": task_name,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{self._gateway_url}/internal/tasks/execute",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                # OIDC authentication for secure service-to-service calls
                "oidc_token": {
                    "service_account_email": self._service_account,
                    "audience": self._gateway_url,
                },
            }
        }
        # Match the worker's request budget explicitly so retry timing is predictable.
        task["dispatch_deadline"] = duration_pb2.Duration(seconds=900)

        # Add schedule time if specified
        if schedule_time:
            timestamp = timestamp_pb2.Timestamp()
            # Ensure timezone-aware datetime
            if schedule_time.tzinfo is None:
                schedule_time = schedule_time.replace(tzinfo=timezone.utc)
            timestamp.FromDatetime(schedule_time)
            task["schedule_time"] = timestamp

        try:
            response = client.create_task(parent=self.queue_path, task=task)
            logger.info(f"Created Cloud Task: {response.name}")
            return response.name
        except AlreadyExists:
            logger.info(f"Cloud Task already exists, reusing name: {task_name}")
            return task_name
        except Exception as e:
            logger.error(f"Failed to create Cloud Task: {e}")
            return None

    def cancel_task(self, task_name: str) -> bool:
        """Cancel a scheduled Cloud Task.

        Args:
            task_name: Full Cloud Task name (returned by create_task)

        Returns:
            True if cancelled successfully, False otherwise
        """
        client = self._get_client()
        if not client:
            return False

        try:
            client.delete_task(name=task_name)
            logger.info(f"Cancelled Cloud Task: {task_name}")
            return True
        except Exception as e:
            # Task may have already been executed or doesn't exist
            logger.warning(f"Could not cancel Cloud Task {task_name}: {e}")
            return False

    def get_task_status(self, task_name: str) -> Optional[dict]:
        """Get the status of a Cloud Task.

        Args:
            task_name: Full Cloud Task name

        Returns:
            Task info dict if found, None otherwise
        """
        client = self._get_client()
        if not client:
            return None

        try:
            task = client.get_task(name=task_name)
            return {
                "name": task.name,
                "schedule_time": _timestamp_to_datetime(getattr(task, "schedule_time", None)),
                "create_time": _timestamp_to_datetime(getattr(task, "create_time", None)),
                "dispatch_count": getattr(task, "dispatch_count", 0),
                "response_count": getattr(task, "response_count", 0),
                "last_attempt": self._format_attempt(getattr(task, "last_attempt", None)),
                "first_attempt": self._format_attempt(getattr(task, "first_attempt", None)),
            }
        except Exception as e:
            logger.warning(f"Could not get Cloud Task {task_name}: {e}")
            return None

    def list_tasks(self) -> list[dict]:
        """List Cloud Tasks in the configured queue."""
        client = self._get_client()
        if not client or not self.queue_path:
            return []

        try:
            tasks = client.list_tasks(parent=self.queue_path)
            return [
                {
                    "name": task.name,
                    "schedule_time": _timestamp_to_datetime(getattr(task, "schedule_time", None)),
                    "create_time": _timestamp_to_datetime(getattr(task, "create_time", None)),
                    "dispatch_count": getattr(task, "dispatch_count", 0),
                    "response_count": getattr(task, "response_count", 0),
                    "last_attempt": self._format_attempt(getattr(task, "last_attempt", None)),
                }
                for task in tasks
            ]
        except Exception as e:
            logger.warning(f"Could not list Cloud Tasks: {e}")
            return []

    def _format_attempt(self, attempt) -> Optional[dict]:
        """Normalize Cloud Task attempt metadata."""
        if not attempt:
            return None

        dispatch_time = _timestamp_to_datetime(getattr(attempt, "dispatch_time", None))
        response_time = _timestamp_to_datetime(getattr(attempt, "response_time", None))
        response_status = getattr(attempt, "response_status", None)

        return {
            "dispatch_time": dispatch_time,
            "response_time": response_time,
            "http_status_code": getattr(response_status, "code", None),
            "http_status_message": getattr(response_status, "message", None),
        }


# Module-level singleton for convenience
_cloud_tasks_client: Optional[CloudTasksClient] = None


def get_cloud_tasks_client() -> CloudTasksClient:
    """Get the singleton Cloud Tasks client instance."""
    global _cloud_tasks_client
    if _cloud_tasks_client is None:
        _cloud_tasks_client = CloudTasksClient()
    return _cloud_tasks_client
