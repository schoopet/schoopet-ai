"""Async Task Tool for spawning and managing background agent tasks.

This tool enables the root agent to:
1. Spawn async tasks that execute in background
2. Schedule tasks for future execution (reminders)
3. Check task status and results

Security: All operations require user_id from ToolContext for proper scoping.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

from ..utils import normalize_user_id
from ..async_tasks.models import (
    AsyncTaskDocument,
    TaskStatus,
    VALID_CHANNELS,
)
from .cloud_tasks_client import get_cloud_tasks_client

logger = logging.getLogger(__name__)

# Firestore collection for async tasks
ASYNC_TASKS_COLLECTION = "async_tasks"


class AsyncTaskTool:
    """Tool for the root agent to spawn and manage async tasks."""

    def __init__(self):
        """Initialize the Async Task Tool - all initialization is deferred."""
        self._firestore_client = None
        self._initialized = False
        self._project_id = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._initialized = True

    def _get_firestore_client(self):
        """Get Firestore client, initializing lazily."""
        if self._firestore_client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import firestore

                self._firestore_client = firestore.Client(project=self._project_id)
        return self._firestore_client

    def _get_user_id(self, tool_context: Optional[ToolContext]) -> Optional[str]:
        """Extract user_id from tool context safely."""
        if not tool_context:
            return None
        if hasattr(tool_context, "user_id") and tool_context.user_id:
            return tool_context.user_id
        return None

    def _get_channel(self, tool_context: Optional[ToolContext]) -> str:
        """Extract notification channel from session state."""
        if not tool_context:
            logger.warning("_get_channel called without tool_context; defaulting to 'sms'")
            return "sms"
        try:
            state = tool_context.state
            if state and "channel" in state:
                channel = state["channel"]
                if channel in VALID_CHANNELS:
                    return channel
        except Exception:
            pass
        user_id = getattr(tool_context, "user_id", "unknown")
        logger.warning(
            "No valid channel in session state for user %s; defaulting to 'sms'. "
            "Session may have been created without a channel in agent state.",
            user_id,
        )
        return "sms"

    def _get_session_id(self, tool_context: Optional[ToolContext]) -> Optional[str]:
        """Extract session_id from tool context safely."""
        if not tool_context:
            return None
        # Try different attribute names based on ADK version
        if hasattr(tool_context, "session_id") and tool_context.session_id:
            return tool_context.session_id
        if hasattr(tool_context, "_invocation_context"):
            invocation = tool_context._invocation_context
            if hasattr(invocation, "session") and hasattr(invocation.session, "id"):
                return invocation.session.id
        return None

    def create_async_task(
        self,
        task_type: str,
        instruction: str,
        context: Optional[Dict[str, Any]] = None,
        schedule_delay_minutes: int = 0,
        schedule_at: Optional[str] = None,
        allowed_resource_ids: Optional[List[str]] = None,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Create an asynchronous task that will execute in the background.

        Use this to delegate long-running tasks or schedule future tasks like reminders.
        The task runs on the deployed Agent Engine with full tool access (calendar, search,
        drive, sheets, memory). You will be notified when the task completes for your review.

        Args:
            task_type: Type of task - one of:
                - "research": In-depth research requiring multiple searches
                - "analysis": Analyzing data, calendars, patterns
                - "reminder": Scheduled reminders at specific times
                - "notification": Future notifications to user
            instruction: Detailed instruction for what the async agent should do.
                Be specific about what information to gather or what action to take.
            context: Additional context from the current conversation (optional).
                Include relevant details the async agent might need.
            schedule_delay_minutes: Delay execution by N minutes (0 = immediate).
                Use for "remind me in 30 minutes" type requests.
            schedule_at: Specific datetime to execute (ISO 8601 format).
                Use for "remind me tomorrow at 9am" type requests.
                Format: "2025-01-12T09:00:00" or "2025-01-12T09:00:00-08:00"
            allowed_resource_ids: Flat list of resource IDs (Sheet IDs, Doc IDs, Drive folder
                IDs) pre-authorized for offline access. Use when the task needs to read/write
                known resources without interrupting the user for confirmation.

        Returns:
            Confirmation message with task ID, or error message if creation failed.

        Examples:
            - Research: create_async_task("research", "Find the best hiking trails near Yosemite with difficulty ratings")
            - Reminder: create_async_task("reminder", "Call mom", schedule_at="2025-01-12T09:00:00")
            - Analysis: create_async_task("analysis", "Look at my calendar and find conflicts next week")
            - Deep research with pre-authorized sheet:
                create_async_task("research", "DEEP_RESEARCH_TASK: ...", allowed_resource_ids=["1BxiM..."])
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot create async task - no user_id available. This tool requires user context."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Async task system not initialized. Check GOOGLE_CLOUD_PROJECT environment variable."

        # Validate task type
        valid_types = ["research", "analysis", "reminder", "notification"]
        if task_type not in valid_types:
            return f"ERROR: Invalid task_type '{task_type}'. Must be one of: {', '.join(valid_types)}"

        # Calculate scheduled time
        scheduled_at_dt = None
        if schedule_at:
            try:
                # Parse ISO format datetime
                scheduled_at_dt = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
                if scheduled_at_dt.tzinfo is None:
                    scheduled_at_dt = scheduled_at_dt.replace(tzinfo=timezone.utc)
            except ValueError as e:
                return f"ERROR: Invalid schedule_at format '{schedule_at}'. Use ISO 8601 format (e.g., '2025-01-12T09:00:00'). Error: {e}"
        elif schedule_delay_minutes > 0:
            scheduled_at_dt = datetime.now(timezone.utc) + timedelta(minutes=schedule_delay_minutes)

        # Generate task ID
        task_id = str(uuid.uuid4())

        # Get current session ID for context
        session_id = self._get_session_id(tool_context)

        # Determine channel from session state
        notification_channel = self._get_channel(tool_context)

        # Create task document
        task = AsyncTaskDocument(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            instruction=instruction,
            context=context or {},
            allowed_resource_ids=allowed_resource_ids or [],
            scheduled_at=scheduled_at_dt,
            notification_channel=notification_channel,
            user_session_id=session_id,
            status=TaskStatus.SCHEDULED if scheduled_at_dt else TaskStatus.PENDING,
        )

        try:
            # Store in Firestore
            doc_ref = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id)
            doc_ref.set(task.to_firestore())

            # Create Cloud Task for execution
            cloud_tasks = get_cloud_tasks_client()
            cloud_task_name = cloud_tasks.create_task(
                task_id=task_id,
                user_id=user_id,
                schedule_time=scheduled_at_dt,
            )

            if cloud_task_name:
                # Update document with Cloud Task reference
                doc_ref.update({"cloud_task_name": cloud_task_name})

                if scheduled_at_dt:
                    time_str = scheduled_at_dt.strftime("%Y-%m-%d at %H:%M UTC")
                    return f"Scheduled {task_type} task for {time_str}. Task ID: {task_id}"
                else:
                    return f"Started async {task_type} task. You'll be notified when it completes. Task ID: {task_id}"
            else:
                # Cloud Task creation failed, but Firestore document exists
                # Task worker can still pick it up if manually triggered
                return f"Created {task_type} task but scheduling may be delayed. Task ID: {task_id}"

        except Exception as e:
            logger.error(f"Failed to create async task: {e}")
            return f"ERROR: Failed to create async task: {str(e)}"

    def check_task_status(
        self,
        task_id: str,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Check the status of an async task.

        Args:
            task_id: The task ID returned by create_async_task

        Returns:
            Status information about the task
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot check task - no user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        try:
            doc = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id).get()

            if not doc.exists:
                return f"Task {task_id} not found."

            data = doc.to_dict()

            # Security: verify task belongs to user
            if data.get("user_id") != user_id:
                return f"Task {task_id} not found."

            task = AsyncTaskDocument.from_firestore(data)

            # Build status message
            status_msg = f"Task: {task.task_type}\nStatus: {task.status.value}"

            if task.status == TaskStatus.SCHEDULED and task.scheduled_at:
                status_msg += f"\nScheduled for: {task.scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}"
            elif task.status in [TaskStatus.COMPLETED, TaskStatus.NOTIFIED] and task.result:
                # Truncate long results
                result_preview = task.result[:200] + "..." if len(task.result) > 200 else task.result
                status_msg += f"\nResult preview: {result_preview}"
            elif task.status == TaskStatus.FAILED and task.error:
                status_msg += f"\nError: {task.error}"

            return status_msg

        except Exception as e:
            logger.error(f"Failed to check task status: {e}")
            return f"ERROR: Failed to check task status: {str(e)}"

    def cancel_task(
        self,
        task_id: str,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Cancel a pending or scheduled async task.

        Can only cancel tasks that haven't started execution yet.

        Args:
            task_id: The task ID to cancel

        Returns:
            Confirmation or error message
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot cancel task - no user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        try:
            doc_ref = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id)
            doc = doc_ref.get()

            if not doc.exists:
                return f"Task {task_id} not found."

            data = doc.to_dict()

            # Security: verify task belongs to user
            if data.get("user_id") != user_id:
                return f"Task {task_id} not found."

            task = AsyncTaskDocument.from_firestore(data)

            if not task.can_cancel():
                return f"Cannot cancel task with status: {task.status.value}"

            # Cancel Cloud Task if exists
            if task.cloud_task_name:
                cloud_tasks = get_cloud_tasks_client()
                cloud_tasks.cancel_task(task.cloud_task_name)

            # Update Firestore
            doc_ref.update({
                "status": TaskStatus.CANCELLED.value,
                "completed_at": datetime.now(timezone.utc),
            })

            return f"Task {task_id} cancelled."

        except Exception as e:
            logger.error(f"Failed to cancel task: {e}")
            return f"ERROR: Failed to cancel task: {str(e)}"

    def list_pending_tasks(
        self,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        List all pending, scheduled, and running tasks for the user.

        Returns:
            List of pending tasks or message if none found
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot list tasks - no user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        try:
            # Query for active tasks
            active_statuses = [
                TaskStatus.PENDING.value,
                TaskStatus.SCHEDULED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.COMPLETED.value,
            ]

            query = (
                firestore_client.collection(ASYNC_TASKS_COLLECTION)
                .where("user_id", "==", user_id)
                .where("status", "in", active_statuses)
                .order_by("created_at")
            )

            docs = query.get()

            if not docs:
                return "No pending tasks."

            result = ["Pending tasks:"]
            for doc in docs:
                data = doc.to_dict()
                task = AsyncTaskDocument.from_firestore(data)

                # Format task info
                instruction_preview = task.instruction[:50] + "..." if len(task.instruction) > 50 else task.instruction
                task_info = f"- [{task.status.value}] {task.task_type}: {instruction_preview}"

                if task.scheduled_at:
                    task_info += f" (scheduled: {task.scheduled_at.strftime('%m/%d %H:%M')})"

                task_info += f" [ID: {task.task_id}]"
                result.append(task_info)

            return "\n".join(result)

        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
            return f"ERROR: Failed to list tasks: {str(e)}"


# Module-level singleton
_async_task_tool: Optional[AsyncTaskTool] = None


def get_async_task_tool() -> AsyncTaskTool:
    """Get the singleton AsyncTaskTool instance."""
    global _async_task_tool
    if _async_task_tool is None:
        _async_task_tool = AsyncTaskTool()
    return _async_task_tool
