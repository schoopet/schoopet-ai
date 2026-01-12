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
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


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
        self._worker_url = None
        self._service_account = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration from environment variables."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self._queue_name = os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks")
        self._worker_url = os.getenv("TASK_WORKER_URL")
        self._service_account = os.getenv(
            "TASK_WORKER_SA",
            f"task-worker@{self._project_id}.iam.gserviceaccount.com"
            if self._project_id
            else None,
        )

        self._initialized = True

        if not self._project_id:
            logger.warning("GOOGLE_CLOUD_PROJECT not set, Cloud Tasks will not work")
        if not self._worker_url:
            logger.warning("TASK_WORKER_URL not set, Cloud Tasks will not work")

    def _get_client(self):
        """Get Cloud Tasks client, initializing lazily."""
        if self._client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import tasks_v2

                self._client = tasks_v2.CloudTasksClient()
        return self._client

    @property
    def queue_path(self) -> Optional[str]:
        """Get the full queue path."""
        self._ensure_initialized()
        client = self._get_client()
        if not client or not self._project_id:
            return None
        return client.queue_path(self._project_id, self._location, self._queue_name)

    def create_task(
        self,
        task_id: str,
        user_id: str,
        schedule_time: Optional[datetime] = None,
    ) -> Optional[str]:
        """Create a Cloud Task to execute an async task.

        Args:
            task_id: The Firestore task document ID (UUID)
            user_id: User's phone number
            schedule_time: When to execute (None = immediate)

        Returns:
            Cloud Task name if successful, None otherwise
        """
        self._ensure_initialized()
        client = self._get_client()

        if not client or not self._worker_url:
            logger.error("Cloud Tasks client not initialized or worker URL not set")
            return None

        # Import here to avoid issues during pickling
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        # Build the task payload
        payload = {"task_id": task_id, "user_id": user_id}

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{self._worker_url}/execute",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                # OIDC authentication for secure service-to-service calls
                "oidc_token": {
                    "service_account_email": self._service_account,
                    "audience": self._worker_url,
                },
            }
        }

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
        except Exception as e:
            logger.error(f"Failed to create Cloud Task: {e}")
            return None

    def create_revision_task(
        self,
        task_id: str,
        user_id: str,
    ) -> Optional[str]:
        """Create a Cloud Task for task revision (after correction request).

        Same as create_task but named for clarity in revision workflow.

        Args:
            task_id: The Firestore task document ID
            user_id: User's phone number

        Returns:
            Cloud Task name if successful, None otherwise
        """
        return self.create_task(task_id=task_id, user_id=user_id, schedule_time=None)

    def create_notification_task(
        self,
        user_id: str,
        task_id: str,
        message: str,
        schedule_time: datetime,
        channel: str = "sms",
    ) -> Optional[str]:
        """Create a Cloud Task for a direct notification (no agent execution).

        Used for simple reminders that don't need agent processing.
        Sends directly to SMS Gateway's notification endpoint.

        Args:
            user_id: User's phone number
            task_id: Associated task ID for tracking
            message: Message to send
            schedule_time: When to send the notification
            channel: Notification channel (sms/whatsapp)

        Returns:
            Cloud Task name if successful, None otherwise
        """
        self._ensure_initialized()
        client = self._get_client()

        if not client:
            logger.error("Cloud Tasks client not initialized")
            return None

        # Get SMS Gateway URL from environment
        sms_gateway_url = os.getenv("SMS_GATEWAY_URL")
        if not sms_gateway_url:
            logger.error("SMS_GATEWAY_URL not set")
            return None

        # Import here to avoid issues during pickling
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        # Build the notification payload
        payload = {
            "user_id": user_id,
            "task_id": task_id,
            "message": message,
            "channel": channel,
        }

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{sms_gateway_url}/internal/user-notify",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                # OIDC authentication
                "oidc_token": {
                    "service_account_email": self._service_account,
                    "audience": sms_gateway_url,
                },
            }
        }

        # Add schedule time
        timestamp = timestamp_pb2.Timestamp()
        if schedule_time.tzinfo is None:
            schedule_time = schedule_time.replace(tzinfo=timezone.utc)
        timestamp.FromDatetime(schedule_time)
        task["schedule_time"] = timestamp

        try:
            response = client.create_task(parent=self.queue_path, task=task)
            logger.info(f"Created notification Cloud Task: {response.name}")
            return response.name
        except Exception as e:
            logger.error(f"Failed to create notification Cloud Task: {e}")
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
                "schedule_time": task.schedule_time.ToDatetime() if task.schedule_time else None,
                "create_time": task.create_time.ToDatetime() if task.create_time else None,
                "dispatch_count": task.dispatch_count,
                "response_count": task.response_count,
            }
        except Exception as e:
            logger.warning(f"Could not get Cloud Task {task_name}: {e}")
            return None


# Module-level singleton for convenience
_cloud_tasks_client: Optional[CloudTasksClient] = None


def get_cloud_tasks_client() -> CloudTasksClient:
    """Get the singleton Cloud Tasks client instance."""
    global _cloud_tasks_client
    if _cloud_tasks_client is None:
        _cloud_tasks_client = CloudTasksClient()
    return _cloud_tasks_client
