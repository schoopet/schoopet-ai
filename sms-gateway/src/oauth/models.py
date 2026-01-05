"""Data models for OAuth management."""
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class OAuthState(BaseModel):
    """Firestore document model for OAuth state (CSRF protection).

    Document ID in Firestore is the state_id (UUID).
    """

    state_id: str = Field(..., description="UUID for state parameter")
    phone_number: str = Field(..., description="E.164 format phone number")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(..., description="State expiration time")
    used: bool = Field(default=False, description="Whether state has been consumed")

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "state_id": self.state_id,
            "phone_number": self.phone_number,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "used": self.used,
        }

    @classmethod
    def from_firestore(cls, data: dict) -> "OAuthState":
        """Create instance from Firestore document data."""
        return cls(
            state_id=data["state_id"],
            phone_number=data["phone_number"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
            used=data.get("used", False),
        )

    def is_valid(self) -> bool:
        """Check if state is valid (not expired and not used)."""
        now = datetime.now(timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return not self.used and now < expires_at


class OAuthToken(BaseModel):
    """Firestore document model for OAuth tokens.

    Document ID in Firestore is the normalized phone number.
    Note: Refresh tokens are stored in Secret Manager, not here.
    """

    phone_number: str = Field(..., description="E.164 format phone number")
    email: str = Field(..., description="Google account email")
    access_token: str = Field(..., description="OAuth access token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_at: datetime = Field(..., description="Access token expiration time")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "phone_number": self.phone_number,
            "email": self.email,
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_firestore(cls, data: dict) -> "OAuthToken":
        """Create instance from Firestore document data."""
        return cls(
            phone_number=data["phone_number"],
            email=data["email"],
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=data["expires_at"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """Check if access token is expired (with buffer for safety)."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now >= (expires_at - timedelta(seconds=buffer_seconds))
