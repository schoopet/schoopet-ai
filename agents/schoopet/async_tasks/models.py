"""Data models for async task management.

These models define the structure of async tasks that can be spawned
by the root agent to execute in background, with results reviewed
by the root agent before being sent to users.
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
    AWAITING_REVIEW = "awaiting_review"  # Waiting for root_agent review
    REVISION_REQUESTED = "revision_requested"  # Root agent requested changes
    APPROVED = "approved"  # Root agent approved the result
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

    # Pre-authorized resources for offline execution (no user confirmation required)
    # Keys are resource_confirmation state_prefix values: "sheet", "doc", "drive_folder"
    allowed_resource_ids: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Resource IDs pre-authorized for offline access, keyed by type",
    )

    # Session tracking
    user_session_id: Optional[str] = Field(
        default=None, description="Original user session for context/notification"
    )
    async_session_id: Optional[str] = Field(
        default=None, description="Async agent's working session ID"
    )
    supervisor_session_id: Optional[str] = Field(
        default=None, description="Root agent's review session ID"
    )

    # Status & Results
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    result: Optional[str] = Field(
        default=None, description="Task result to deliver to user"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")

    # Review loop
    review_attempts: int = Field(default=0, description="Number of review cycles")
    max_review_attempts: int = Field(
        default=3, description="Maximum revision attempts before auto-failing"
    )
    revision_feedback: Optional[str] = Field(
        default=None, description="Root agent's correction request"
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
    reviewed_at: Optional[datetime] = Field(
        default=None, description="When root agent reviewed/approved"
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
            "review_attempts": self.review_attempts,
            "max_review_attempts": self.max_review_attempts,
            "created_at": self.created_at,
        }

        if self.allowed_resource_ids:
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
        if self.supervisor_session_id:
            data["supervisor_session_id"] = self.supervisor_session_id
        if self.result:
            data["result"] = self.result
        if self.error:
            data["error"] = self.error
        if self.revision_feedback:
            data["revision_feedback"] = self.revision_feedback
        if self.started_at:
            data["started_at"] = self.started_at
        if self.completed_at:
            data["completed_at"] = self.completed_at
        if self.reviewed_at:
            data["reviewed_at"] = self.reviewed_at
        if self.notified_at:
            data["notified_at"] = self.notified_at

        return data

    @classmethod
    def from_firestore(cls, data: dict) -> "AsyncTaskDocument":
        """Create instance from Firestore document data."""
        return cls(
            task_id=data["task_id"],
            user_id=data["user_id"],
            task_type=data["task_type"],
            instruction=data["instruction"],
            context=data.get("context", {}),
            allowed_resource_ids=data.get("allowed_resource_ids", {}),
            scheduled_at=data.get("scheduled_at"),
            cloud_task_name=data.get("cloud_task_name"),
            agent_type=data.get("agent_type", "personal"),
            notification_channel=data.get("notification_channel", "sms"),
            user_session_id=data.get("user_session_id"),
            async_session_id=data.get("async_session_id"),
            supervisor_session_id=data.get("supervisor_session_id"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
            result=data.get("result"),
            error=data.get("error"),
            review_attempts=data.get("review_attempts", 0),
            max_review_attempts=data.get("max_review_attempts", 3),
            revision_feedback=data.get("revision_feedback"),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            reviewed_at=data.get("reviewed_at"),
            notified_at=data.get("notified_at"),
        )

    def can_execute(self) -> bool:
        """Check if task can be executed (pending or revision requested)."""
        return self.status in [TaskStatus.PENDING, TaskStatus.REVISION_REQUESTED]

    def can_review(self) -> bool:
        """Check if task is ready for review."""
        return self.status == TaskStatus.AWAITING_REVIEW

    def can_cancel(self) -> bool:
        """Check if task can be cancelled."""
        return self.status in [
            TaskStatus.PENDING,
            TaskStatus.SCHEDULED,
            TaskStatus.RUNNING,
        ]


class TaskReviewRequest(BaseModel):
    """Request payload for internal task review notification.

    Sent from Task Worker to SMS Gateway when async task completes.
    """

    task_id: str = Field(..., description="Task ID to review")
    user_id: str = Field(..., description="User's phone number")
    result: Optional[str] = Field(default=None, description="Task result")
    error: Optional[str] = Field(default=None, description="Error if failed")


class UserNotifyRequest(BaseModel):
    """Request payload for user notification.

    Sent when root agent approves a task and user needs to be notified.
    """

    user_id: str = Field(..., description="User's phone number")
    task_id: str = Field(..., description="Task ID that was completed")
    message: str = Field(..., description="Message to send to user")
    channel: str = Field(default="sms", description="Notification channel (sms/whatsapp)")


class SupervisorSessionDocument(BaseModel):
    """Firestore document model for supervisor sessions.

    Document ID in Firestore is: {normalized_phone}_supervisor_{task_id}
    Collection: supervisor_sessions
    """

    phone_number: str = Field(..., description="E.164 format phone number")
    agent_session_id: str = Field(..., description="Vertex AI Agent Engine session ID")
    session_type: str = Field(default="supervisor", description="Session type identifier")
    task_id: str = Field(..., description="Associated task ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "phone_number": self.phone_number,
            "agent_session_id": self.agent_session_id,
            "session_type": self.session_type,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
        }

    @classmethod
    def from_firestore(cls, data: dict) -> "SupervisorSessionDocument":
        """Create instance from Firestore document data."""
        return cls(
            phone_number=data["phone_number"],
            agent_session_id=data["agent_session_id"],
            session_type=data.get("session_type", "supervisor"),
            task_id=data["task_id"],
            created_at=data["created_at"],
            last_activity=data.get("last_activity", data["created_at"]),
        )
