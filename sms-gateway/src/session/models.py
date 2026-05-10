"""Data models for session management."""
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class SessionDocument(BaseModel):
    """Firestore document model for gateway sessions.

    Document ID in Firestore is the normalized user identifier.
    """

    phone_number: str = Field(..., description="User identifier")
    agent_session_id: str = Field(default="", description="Legacy field kept for backward-compat reads")
    personal_agent_session_id: str = Field(default="", description="Vertex AI agent session ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message_count: int = Field(default=0)
    opted_in: bool = Field(default=False, description="Whether user has opted in to receive messages")
    opt_in_requested_at: Optional[datetime] = Field(default=None, description="When opt-in was first requested")
    channel: str = Field(default="discord", description="Last used channel: discord, telegram, slack, etc.")
    session_scope: str = Field(default="", description="Optional channel/thread scope for scoped sessions")
    state_extra: dict = Field(default_factory=dict, description="Extra state passed to Agent Engine session")
    slack_team_id: Optional[str] = Field(default=None, description="Slack workspace team_id")
    pending_confirmations: list[dict] = Field(
        default_factory=list,
        description="Live pending ADK approvals awaiting user interaction",
    )

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        data = {
            "phone_number": self.phone_number,
            "personal_agent_session_id": self.personal_agent_session_id,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "message_count": self.message_count,
            "opted_in": self.opted_in,
            "channel": self.channel,
            "session_scope": self.session_scope,
            "state_extra": self.state_extra,
        }
        if self.opt_in_requested_at:
            data["opt_in_requested_at"] = self.opt_in_requested_at
        if self.slack_team_id:
            data["slack_team_id"] = self.slack_team_id
        if self.pending_confirmations:
            data["pending_confirmations"] = self.pending_confirmations
        return data

    @classmethod
    def from_firestore(cls, data: dict) -> "SessionDocument":
        """Create instance from Firestore document data."""
        return cls(
            phone_number=data["phone_number"],
            agent_session_id=data.get("agent_session_id", ""),
            personal_agent_session_id=data.get("personal_agent_session_id", ""),
            created_at=data["created_at"],
            last_activity=data["last_activity"],
            message_count=data.get("message_count", 0),
            opted_in=data.get("opted_in", False),
            opt_in_requested_at=data.get("opt_in_requested_at"),
            channel=data.get("channel", "discord"),
            session_scope=data.get("session_scope", ""),
            state_extra=data.get("state_extra", {}),
            slack_team_id=data.get("slack_team_id"),
            pending_confirmations=data.get("pending_confirmations", []),
        )


class SessionInfo(BaseModel):
    """Session information returned by SessionManager."""

    phone_number: str
    agent_session_id: str
    is_new_session: bool = False
    opted_in: bool = False
    is_new_user: bool = False
    channel: str = Field(default="discord", description="Communication channel: discord, telegram, slack, etc.")
    session_scope: str = Field(default="", description="Optional channel/thread scope for scoped sessions")
    state_extra: dict = Field(default_factory=dict, description="Extra session routing metadata")
