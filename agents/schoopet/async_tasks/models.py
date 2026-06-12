"""Data models for async task management.

These models define the structure of async tasks that can be spawned
by the root agent to execute in background, with results delivered
directly to users upon completion.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Status of an async task throughout its lifecycle."""

    PENDING = "pending"  # Created, waiting to execute
    SCHEDULED = "scheduled"  # Cloud Task created, waiting for scheduled time
    RUNNING = "running"  # Currently executing
    COMPLETED = "completed"  # Execution done, result persisted (notification may be in flight)
    NOTIFIED = "notified"  # User has been notified of completion
    FAILED = "failed"  # Failed with error
    CANCELLED = "cancelled"  # User or agent cancelled


class AsyncTaskDocument(BaseModel):
    """Firestore document model for async tasks.

    Document ID in Firestore is the task_id (UUID).
    Collection: async_tasks
    """

    # Identity
    task_id: str = Field(..., description="Unique task identifier (UUID)")
    user_id: str = Field(..., description="User identifier")

    # Task definition
    task_type: str = Field(
        ..., description="Type of async task (research, analysis, reminder, notification)"
    )
    instruction: str = Field(..., description="Detailed instruction for async agent")
    context: Dict[str, Any] = Field(
        default_factory=dict, description="Additional context from conversation"
    )

    # Scheduling
    scheduled_at: Optional[datetime] = Field(
        default=None, description="When to execute (None = immediate)"
    )
    recurrence_hours: Optional[float] = Field(
        default=None, description="Repeat interval in hours (e.g. 24 for daily, 6 for every 6 hours)"
    )
    cloud_task_name: Optional[str] = Field(
        default=None, description="Cloud Tasks task name for tracking/cancellation"
    )

    # Routing
    notification_session_scope: str = Field(
        default="", description="Optional scoped session to use for completion notification"
    )
    notification_target_type: str = Field(
        default="", description="Optional target type such as discord_channel"
    )
    discord_channel_id: str = Field(
        default="", description="Discord channel ID for channel-scoped notifications"
    )
    discord_channel_name: str = Field(
        default="", description="Discord channel name for channel-scoped notifications"
    )
    target_channel_id: str = Field(
        default="", description="Override Discord channel for completion delivery"
    )

    allowed_resource_ids: List[str] = Field(
        default_factory=list,
        description="Resource IDs pre-authorized for offline access (flat list of IDs)",
    )

    # Status & Results
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    result: Optional[str] = Field(
        default=None, description="Task result to deliver to user"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")
    attempts: int = Field(default=0, description="Number of execution attempts")
    last_tool_call: Optional[str] = Field(
        default=None, description="Most recent tool called during execution"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When task was created",
    )
    started_at: Optional[datetime] = Field(
        default=None, description="When execution started"
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="When execution completed (result ready)"
    )
    notified_at: Optional[datetime] = Field(
        default=None, description="When user was notified"
    )
    last_event_at: Optional[datetime] = Field(
        default=None, description="Most recent progress event during execution"
    )

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        data = {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "task_type": self.task_type,
            "instruction": self.instruction,
            "context": self.context,
            "notification_session_scope": self.notification_session_scope,
            "notification_target_type": self.notification_target_type,
            "discord_channel_id": self.discord_channel_id,
            "discord_channel_name": self.discord_channel_name,
            "target_channel_id": self.target_channel_id,
            "status": self.status.value,
            "created_at": self.created_at,
        }

        data["allowed_resource_ids"] = self.allowed_resource_ids

        # Add optional fields if set
        if self.scheduled_at:
            data["scheduled_at"] = self.scheduled_at
        if self.recurrence_hours is not None:
            data["recurrence_hours"] = self.recurrence_hours
        if self.cloud_task_name:
            data["cloud_task_name"] = self.cloud_task_name
        if self.result:
            data["result"] = self.result
        if self.error:
            data["error"] = self.error
        if self.attempts:
            data["attempts"] = self.attempts
        if self.last_tool_call:
            data["last_tool_call"] = self.last_tool_call
        if self.started_at:
            data["started_at"] = self.started_at
        if self.completed_at:
            data["completed_at"] = self.completed_at
        if self.notified_at:
            data["notified_at"] = self.notified_at
        if self.last_event_at:
            data["last_event_at"] = self.last_event_at

        return data

    @classmethod
    def from_firestore(cls, data: dict) -> "AsyncTaskDocument":
        """Create instance from Firestore document data."""
        # Map legacy review statuses to COMPLETED for backward compat with existing docs
        raw_status = data.get("status", TaskStatus.PENDING.value)
        if raw_status in ("awaiting_review", "approved", "revision_requested"):
            raw_status = TaskStatus.COMPLETED.value
        return cls(
            task_id=data["task_id"],
            user_id=data["user_id"],
            task_type=data["task_type"],
            instruction=data["instruction"],
            context=data.get("context", {}),
            allowed_resource_ids=data.get("allowed_resource_ids", []),
            scheduled_at=data.get("scheduled_at"),
            recurrence_hours=data.get("recurrence_hours"),
            cloud_task_name=data.get("cloud_task_name"),
            notification_session_scope=data.get("notification_session_scope", ""),
            notification_target_type=data.get("notification_target_type", ""),
            discord_channel_id=data.get("discord_channel_id", ""),
            discord_channel_name=data.get("discord_channel_name", ""),
            target_channel_id=data.get("target_channel_id", ""),
            status=TaskStatus(raw_status),
            result=data.get("result"),
            error=data.get("error"),
            attempts=data.get("attempts", 0),
            last_tool_call=data.get("last_tool_call"),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            notified_at=data.get("notified_at"),
            last_event_at=data.get("last_event_at"),
        )

    def can_cancel(self) -> bool:
        """Check if task can be cancelled."""
        return self.status in [
            TaskStatus.PENDING,
            TaskStatus.SCHEDULED,
            TaskStatus.RUNNING,
        ]
