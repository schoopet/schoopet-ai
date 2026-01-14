"""Async Task Tool for spawning and managing background agent tasks.

This tool enables the root agent to:
1. Spawn async tasks that execute in background
2. Schedule tasks for future execution (reminders)
3. Check task status and results
4. Review and approve completed tasks (supervisor functions)
5. Request corrections from async agents

Security: All operations require user_id from ToolContext for proper scoping.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

from ..async_tasks.models import (
    AsyncTaskDocument,
    MemoryIsolation,
    TaskStatus,
)
from .cloud_tasks_client import get_cloud_tasks_client

logger = logging.getLogger(__name__)

# Firestore collection for async tasks
ASYNC_TASKS_COLLECTION = "async_tasks"


class AsyncTaskTool:
    """Tool for the root agent to spawn and manage async tasks.

    Provides both task creation functions (used in user sessions) and
    supervisor functions (used in supervisor sessions to review results).
    """

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

    def _normalize_phone(self, phone_number: str) -> str:
        """Normalize phone number for consistent document IDs."""
        return phone_number.lstrip("+").replace("-", "").replace(" ", "")

    def _get_user_id(self, tool_context: Optional[ToolContext]) -> Optional[str]:
        """Extract user_id from tool context safely."""
        if not tool_context:
            return None
        if hasattr(tool_context, "user_id") and tool_context.user_id:
            return tool_context.user_id
        return None

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

    # ========== Task Creation Functions (User Session) ==========

    def create_async_task(
        self,
        task_type: str,
        instruction: str,
        context: Optional[Dict[str, Any]] = None,
        schedule_delay_minutes: int = 0,
        schedule_at: Optional[str] = None,
        memory_isolation: str = "shared",
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Create an asynchronous task that will execute in the background.

        Use this to delegate long-running tasks or schedule future tasks like reminders.
        You will be notified when the task completes for your review before the user sees results.

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
            memory_isolation: Memory access level for the task:
                - "shared": Full access to user's Memory Bank (default, best for most tasks)
                - "isolated": Separate session, results synced on completion (parallel work)
                - "readonly": Can read memories but not write (analysis without side effects)

        Returns:
            Confirmation message with task ID, or error message if creation failed.

        Examples:
            - Research: create_async_task("research", "Find the best hiking trails near Yosemite with difficulty ratings")
            - Reminder: create_async_task("reminder", "Call mom", schedule_at="2025-01-12T09:00:00")
            - Analysis: create_async_task("analysis", "Look at my calendar and find conflicts next week", memory_isolation="readonly")
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

        # Validate memory isolation
        try:
            mem_isolation = MemoryIsolation(memory_isolation)
        except ValueError:
            return f"ERROR: Invalid memory_isolation '{memory_isolation}'. Must be one of: shared, isolated, readonly"

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

        # Create task document
        task = AsyncTaskDocument(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            instruction=instruction,
            context=context or {},
            scheduled_at=scheduled_at_dt,
            memory_isolation=mem_isolation,
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
                    return f"Started async {task_type} task. You'll receive the result for review when complete. Task ID: {task_id}"
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
            elif task.status == TaskStatus.COMPLETED and task.result:
                # Truncate long results
                result_preview = task.result[:200] + "..." if len(task.result) > 200 else task.result
                status_msg += f"\nResult preview: {result_preview}"
            elif task.status == TaskStatus.AWAITING_REVIEW:
                status_msg += "\nWaiting for review before delivery."
            elif task.status == TaskStatus.FAILED and task.error:
                status_msg += f"\nError: {task.error}"
            elif task.status == TaskStatus.REVISION_REQUESTED:
                status_msg += f"\nRevision requested: {task.revision_feedback or 'See supervisor notes'}"

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
                TaskStatus.AWAITING_REVIEW.value,
                TaskStatus.REVISION_REQUESTED.value,
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

                task_info += f" [ID: {task.task_id[:8]}...]"
                result.append(task_info)

            return "\n".join(result)

        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
            return f"ERROR: Failed to list tasks: {str(e)}"

    # ========== Supervisor Functions (Supervisor Session) ==========

    def review_task_result(
        self,
        task_id: str,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Review the result of a completed async task.

        This is called in the supervisor session when an async task completes.
        Use this to evaluate the result before deciding to approve or request corrections.

        Args:
            task_id: The task ID to review

        Returns:
            Full task details including the result for review
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot review task - no user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        try:
            doc = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id).get()

            if not doc.exists:
                return f"Task {task_id} not found."

            data = doc.to_dict()

            # Security: verify task belongs to user (supervisor session has user_id)
            # Note: supervisor user_id format might be "{phone}_supervisor"
            task_user = data.get("user_id", "")
            check_user = user_id.replace("_supervisor", "")
            if task_user != check_user and task_user != user_id:
                return f"Task {task_id} not found."

            task = AsyncTaskDocument.from_firestore(data)

            # Build detailed review info
            review = [
                f"=== Task Review: {task.task_type} ===",
                f"Task ID: {task.task_id}",
                f"Status: {task.status.value}",
                f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
                "",
                "Original Instruction:",
                task.instruction,
                "",
            ]

            if task.context:
                review.append("Context provided:")
                for key, value in task.context.items():
                    review.append(f"  {key}: {value}")
                review.append("")

            if task.result:
                review.append("=== RESULT ===")
                review.append(task.result)
                review.append("")

            if task.error:
                review.append("=== ERROR ===")
                review.append(task.error)
                review.append("")

            if task.revision_feedback:
                review.append(f"Previous revision feedback: {task.revision_feedback}")
                review.append(f"Review attempts: {task.review_attempts}/{task.max_review_attempts}")
                review.append("")

            review.append("=== Review Actions ===")
            if task.status == TaskStatus.AWAITING_REVIEW:
                review.append("- Use approve_task(task_id) to approve and notify user")
                review.append("- Use request_correction(task_id, feedback) to request revision")
            else:
                review.append(f"Note: Task is in '{task.status.value}' status")

            return "\n".join(review)

        except Exception as e:
            logger.error(f"Failed to review task: {e}")
            return f"ERROR: Failed to review task: {str(e)}"

    def approve_task(
        self,
        task_id: str,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Approve a task result and trigger user notification.

        Call this after reviewing a task result that meets the user's request.
        The user will receive the result via SMS/WhatsApp.

        Args:
            task_id: The task ID to approve

        Returns:
            Confirmation message
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot approve task - no user_id available."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        try:
            doc_ref = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id)
            doc = doc_ref.get()

            if not doc.exists:
                return f"Task {task_id} not found."

            data = doc.to_dict()
            task = AsyncTaskDocument.from_firestore(data)

            # Verify authorization
            task_user = data.get("user_id", "")
            check_user = user_id.replace("_supervisor", "")
            if task_user != check_user and task_user != user_id:
                return f"Task {task_id} not found."

            if task.status != TaskStatus.AWAITING_REVIEW:
                return f"Cannot approve task with status: {task.status.value}. Only tasks awaiting review can be approved."

            # Update task status
            now = datetime.now(timezone.utc)
            doc_ref.update({
                "status": TaskStatus.APPROVED.value,
                "reviewed_at": now,
            })

            # Trigger user notification via Cloud Tasks
            if task.result:
                cloud_tasks = get_cloud_tasks_client()
                cloud_tasks.create_notification_task(
                    user_id=task_user,
                    task_id=task_id,
                    message=task.result,
                    schedule_time=now,  # Immediate
                    channel="sms",
                )

                # Update notified status
                doc_ref.update({
                    "status": TaskStatus.NOTIFIED.value,
                    "notified_at": now,
                })

                return f"Task {task_id} approved. User will be notified with the result."
            else:
                return f"Task {task_id} approved but has no result to send."

        except Exception as e:
            logger.error(f"Failed to approve task: {e}")
            return f"ERROR: Failed to approve task: {str(e)}"

    def request_correction(
        self,
        task_id: str,
        feedback: str,
        tool_context: Optional[ToolContext] = None,
    ) -> str:
        """
        Request corrections to a task result.

        Call this when the task result doesn't meet the user's needs.
        Provide specific feedback about what needs to be improved.
        The async agent will revise the result based on your feedback.

        Args:
            task_id: The task ID to request correction for
            feedback: Specific feedback about what needs improvement.
                Be clear and actionable (e.g., "Add price ranges for each restaurant"
                or "Include distance from downtown")

        Returns:
            Confirmation message
        """
        user_id = self._get_user_id(tool_context)
        if not user_id:
            return "ERROR: Cannot request correction - no user_id available."

        if not feedback or not feedback.strip():
            return "ERROR: Please provide specific feedback about what needs improvement."

        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return "ERROR: Task system not initialized."

        try:
            doc_ref = firestore_client.collection(ASYNC_TASKS_COLLECTION).document(task_id)
            doc = doc_ref.get()

            if not doc.exists:
                return f"Task {task_id} not found."

            data = doc.to_dict()
            task = AsyncTaskDocument.from_firestore(data)

            # Verify authorization
            task_user = data.get("user_id", "")
            check_user = user_id.replace("_supervisor", "")
            if task_user != check_user and task_user != user_id:
                return f"Task {task_id} not found."

            if task.status != TaskStatus.AWAITING_REVIEW:
                return f"Cannot request correction for task with status: {task.status.value}"

            # Check if max revisions reached
            if task.review_attempts >= task.max_review_attempts:
                return f"Task has reached maximum revision attempts ({task.max_review_attempts}). Please approve with current result or cancel the task."

            # Update task with feedback
            doc_ref.update({
                "status": TaskStatus.REVISION_REQUESTED.value,
                "revision_feedback": feedback,
                "review_attempts": task.review_attempts + 1,
            })

            # Create Cloud Task for revision execution
            cloud_tasks = get_cloud_tasks_client()
            cloud_task_name = cloud_tasks.create_revision_task(
                task_id=task_id,
                user_id=task_user,
            )

            if cloud_task_name:
                doc_ref.update({"cloud_task_name": cloud_task_name})
                return f"Revision requested for task {task_id}. Feedback: '{feedback}'. Attempt {task.review_attempts + 1}/{task.max_review_attempts}."
            else:
                return f"Revision requested but scheduling may be delayed. Task ID: {task_id}"

        except Exception as e:
            logger.error(f"Failed to request correction: {e}")
            return f"ERROR: Failed to request correction: {str(e)}"


# Module-level singleton
_async_task_tool: Optional[AsyncTaskTool] = None


def get_async_task_tool() -> AsyncTaskTool:
    """Get the singleton AsyncTaskTool instance."""
    global _async_task_tool
    if _async_task_tool is None:
        _async_task_tool = AsyncTaskTool()
    return _async_task_tool
