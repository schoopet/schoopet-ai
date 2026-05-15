"""Task debugging and scheduling observability tools."""
from datetime import datetime, timezone
from typing import Optional

from google.adk.tools import ToolContext

from .async_tasks.models import AsyncTaskDocument
from .tools.async_task_tool import ASYNC_TASKS_COLLECTION
from .tools.cloud_tasks_client import get_cloud_tasks_client


class TaskDebugTool:
    """Operational tools for inspecting Firestore tasks and Cloud Tasks state."""

    def __init__(self):
        self._firestore_client = None
        self._initialized = False
        self._project_id = None

    def _ensure_initialized(self):
        if self._initialized:
            return

        import os

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._initialized = True

    def _get_firestore_client(self):
        if self._firestore_client is None:
            self._ensure_initialized()
            if self._project_id:
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=self._project_id)
        return self._firestore_client

    def _get_user_id(self, tool_context: Optional[ToolContext]) -> Optional[str]:
        if not tool_context:
            return None
        return getattr(tool_context, "user_id", None)

    def _load_task(self, task_id: str, tool_context: Optional[ToolContext]) -> tuple[Optional[dict], Optional[str]]:
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return None, "ERROR: No user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return None, "ERROR: Task system not initialized."

        doc = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id).get()
        if not doc.exists:
            return None, f"Task {task_id} not found."

        data = doc.to_dict()
        task_user = data.get("user_id", "")
        check_user = user_id.replace("_supervisor", "")
        if task_user != check_user and task_user != user_id:
            return None, f"Task {task_id} not found."

        return data, None

    def get_cloud_task_status(
        self,
        task_id: str = None,
        cloud_task_name: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Get Cloud Task scheduling/execution metadata for an async task."""
        if not task_id and not cloud_task_name:
            return "ERROR: Provide task_id or cloud_task_name."

        firestore_data = None
        if task_id:
            firestore_data, error = self._load_task(task_id, tool_context)
            if error:
                return error
            cloud_task_name = cloud_task_name or firestore_data.get("cloud_task_name")
            if not cloud_task_name:
                return f"Task {task_id} has no cloud_task_name recorded."

        cloud_tasks = get_cloud_tasks_client()
        status = cloud_tasks.get_task_status(cloud_task_name)
        if not status:
            if task_id and firestore_data:
                return (
                    f"Cloud Task not found for task {task_id}.\n"
                    f"Firestore status: {firestore_data.get('status')}\n"
                    f"cloud_task_name: {cloud_task_name}"
                )
            return f"Cloud Task not found: {cloud_task_name}"

        lines = [
            f"Cloud Task: {status['name']}",
            f"Schedule time: {self._format_dt(status.get('schedule_time'))}",
            f"Create time: {self._format_dt(status.get('create_time'))}",
            f"Dispatch count: {status.get('dispatch_count', 0)}",
            f"Response count: {status.get('response_count', 0)}",
        ]

        if status.get("last_attempt"):
            lines.extend(self._format_attempt_lines("Last attempt", status["last_attempt"]))
        if status.get("first_attempt"):
            lines.extend(self._format_attempt_lines("First attempt", status["first_attempt"]))

        if firestore_data:
            lines.append(f"Firestore status: {firestore_data.get('status')}")

        return "\n".join(lines)

    def list_scheduled_tasks(
        self,
        limit: int = 10,
        tool_context: ToolContext = None,
    ) -> str:
        """List the user's scheduled tasks with upcoming execution times."""
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot list tasks - no user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        docs = (
            firestore_client.collection(ASYNC_TASKS_COLLECTION)
            .where("user_id", "==", user_id)
            .where("status", "==", "scheduled")
            .order_by("scheduled_at")
            .limit(limit)
            .get()
        )

        if not docs:
            return "No scheduled tasks."

        now = datetime.now(timezone.utc)
        cloud_tasks = get_cloud_tasks_client()
        lines = ["Scheduled tasks:"]
        for doc in docs:
            data = doc.to_dict()
            task = AsyncTaskDocument.from_firestore(data)
            cloud_status = (
                cloud_tasks.get_task_status(task.cloud_task_name)
                if task.cloud_task_name
                else None
            )
            overdue = task.scheduled_at and task.scheduled_at < now
            preview = task.instruction[:80] + "..." if len(task.instruction) > 80 else task.instruction
            line = (
                f"- {task.task_id}: {task.task_type} at "
                f"{self._format_dt(task.scheduled_at)}"
            )
            if overdue:
                line += " [OVERDUE]"
            line += f" | {preview}"
            lines.append(line)
            if task.cloud_task_name:
                lines.append(f"  Cloud Task: {task.cloud_task_name}")
            if cloud_status:
                lines.append(
                    "  Queue status: "
                    f"dispatch_count={cloud_status.get('dispatch_count', 0)}, "
                    f"response_count={cloud_status.get('response_count', 0)}, "
                    f"schedule_time={self._format_dt(cloud_status.get('schedule_time'))}"
                )

        return "\n".join(lines)

    def debug_task(
        self,
        task_id: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Build a combined Firestore + Cloud Tasks debug report for a task."""
        data, error = self._load_task(task_id, tool_context)
        if error:
            return error

        task = AsyncTaskDocument.from_firestore(data)
        lines = [
            f"Task ID: {task.task_id}",
            f"Type: {task.task_type}",
            f"Status: {task.status.value}",
            f"Created: {self._format_dt(task.created_at)}",
            f"Scheduled: {self._format_dt(task.scheduled_at)}",
            f"Started: {self._format_dt(task.started_at)}",
            f"Completed: {self._format_dt(task.completed_at)}",
            f"Instruction: {task.instruction}",
        ]

        if task.error:
            lines.append(f"Error: {task.error}")
        if task.cloud_task_name:
            lines.append(f"Cloud Task name: {task.cloud_task_name}")
            cloud_status = get_cloud_tasks_client().get_task_status(task.cloud_task_name)
            if cloud_status:
                lines.append("--- Cloud Task ---")
                lines.append(f"Schedule time: {self._format_dt(cloud_status.get('schedule_time'))}")
                lines.append(f"Create time: {self._format_dt(cloud_status.get('create_time'))}")
                lines.append(f"Dispatch count: {cloud_status.get('dispatch_count', 0)}")
                lines.append(f"Response count: {cloud_status.get('response_count', 0)}")
                if cloud_status.get("last_attempt"):
                    lines.extend(self._format_attempt_lines("Last attempt", cloud_status["last_attempt"]))
            else:
                lines.append("--- Cloud Task ---")
                lines.append("Cloud Task metadata unavailable or task no longer exists in the queue.")
        else:
            lines.append("Cloud Task name: none recorded")

        return "\n".join(lines)

    def _format_dt(self, value: Optional[datetime]) -> str:
        if not value:
            return "None"
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _format_attempt_lines(self, label: str, attempt: dict) -> list[str]:
        return [
            f"{label} dispatch: {self._format_dt(attempt.get('dispatch_time'))}",
            f"{label} response: {self._format_dt(attempt.get('response_time'))}",
            f"{label} HTTP status: {attempt.get('http_status_code')} {attempt.get('http_status_message') or ''}".rstrip(),
        ]
