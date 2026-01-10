"""Data models for session management."""
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class SessionDocument(BaseModel):
    """Firestore document model for SMS sessions.

    Document ID in Firestore is the normalized phone number.
    """

    phone_number: str = Field(..., description="E.164 format phone number")
    agent_session_id: str = Field(default="", description="Vertex AI Agent Engine session ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = Field(default=0)
    opted_in: bool = Field(default=False, description="Whether user has opted in to receive messages")
    opt_in_requested_at: Optional[datetime] = Field(default=None, description="When opt-in was first requested")

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        data = {
            "phone_number": self.phone_number,
            "agent_session_id": self.agent_session_id,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "message_count": self.message_count,
            "opted_in": self.opted_in,
        }
        if self.opt_in_requested_at:
            data["opt_in_requested_at"] = self.opt_in_requested_at
        return data

    @classmethod
    def from_firestore(cls, data: dict) -> "SessionDocument":
        """Create instance from Firestore document data."""
        return cls(
            phone_number=data["phone_number"],
            agent_session_id=data.get("agent_session_id", ""),
            created_at=data["created_at"],
            last_activity=data["last_activity"],
            message_count=data.get("message_count", 0),
            opted_in=data.get("opted_in", False),
            opt_in_requested_at=data.get("opt_in_requested_at"),
        )


class SessionInfo(BaseModel):
    """Session information returned by SessionManager."""

    phone_number: str
    agent_session_id: str
    is_new_session: bool = False
    opted_in: bool = False
    is_new_user: bool = False
