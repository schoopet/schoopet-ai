"""Data models for async task management.

These models define the structure of async tasks that can be spawned
by the root agent to execute in background, with results delivered
directly to users upon completion.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# Single source of truth for valid notification channels and agent types.
# Add new channels here when adding a new messaging integration.
VALID_CHANNELS = {"sms", "whatsapp", "telegram", "discord", "slack", "email"}
VALID_AGENT_TYPES = {"personal", "team"}


class TaskStatus(str, Enum):
    """Status of an async task throughout its lifecycle."""

    PENDING = "pending"  # Created, waiting to execute
    SCHEDULED = "scheduled"  # Cloud Task created, waiting for scheduled time
    RUNNING = "running"  # Currently executing
    COMPLETED = "completed"  # Execution done, delivering to user
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
    user_id: str = Field(..., description="Phone number of the user (E.164 format)")

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
    cloud_task_name: Optional[str] = Field(
        default=None, description="Cloud Tasks task name for tracking/cancellation"
    )

    # Routing
    agent_type: str = Field(
        default="personal", description="Agent engine to use: personal or team"
    )
    notification_channel: str = Field(
        default="sms", description="Channel to notify user on completion: sms, discord, slack, etc."
    )

    allowed_resource_ids: List[str] = Field(
        default_factory=list,
        description="Resource IDs pre-authorized for offline access (flat list of IDs)",
    )

    # Session tracking
    user_session_id: Optional[str] = Field(
        default=None, description="Original user session for context/notification"
    )
    async_session_id: Optional[str] = Field(
        default=None, description="Async agent's working session ID"
    )

    # Status & Results
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    result: Optional[str] = Field(
        default=None, description="Task result to deliver to user"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")

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

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        data = {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "task_type": self.task_type,
            "instruction": self.instruction,
            "context": self.context,
            "agent_type": self.agent_type,
            "notification_channel": self.notification_channel,
            "status": self.status.value,
            "created_at": self.created_at,
        }

        data["allowed_resource_ids"] = self.allowed_resource_ids

        # Add optional fields if set
        if self.scheduled_at:
            data["scheduled_at"] = self.scheduled_at
        if self.cloud_task_name:
            data["cloud_task_name"] = self.cloud_task_name
        if self.user_session_id:
            data["user_session_id"] = self.user_session_id
        if self.async_session_id:
            data["async_session_id"] = self.async_session_id
        if self.result:
            data["result"] = self.result
        if self.error:
            data["error"] = self.error
        if self.started_at:
            data["started_at"] = self.started_at
        if self.completed_at:
            data["completed_at"] = self.completed_at
        if self.notified_at:
            data["notified_at"] = self.notified_at

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
            cloud_task_name=data.get("cloud_task_name"),
            agent_type=data.get("agent_type", "personal"),
            notification_channel=data.get("notification_channel", "sms"),
            user_session_id=data.get("user_session_id"),
            async_session_id=data.get("async_session_id"),
            status=TaskStatus(raw_status),
            result=data.get("result"),
            error=data.get("error"),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            notified_at=data.get("notified_at"),
        )

    def can_execute(self) -> bool:
        """Check if task can be executed."""
        return self.status == TaskStatus.PENDING

    def can_cancel(self) -> bool:
        """Check if task can be cancelled."""
        return self.status in [
            TaskStatus.PENDING,
            TaskStatus.SCHEDULED,
            TaskStatus.RUNNING,
        ]


