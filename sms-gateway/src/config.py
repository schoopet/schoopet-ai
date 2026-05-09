"""Configuration settings for SMS Gateway."""
import os
from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Google Cloud Configuration
    GOOGLE_CLOUD_PROJECT: str
    GOOGLE_CLOUD_LOCATION: str = "us-central1"
    PERSONAL_AGENT_ENGINE_ID: str = ""

    # Service Configuration
    SESSION_TIMEOUT_MINUTES: int = 30
    AGENT_TIMEOUT_SECONDS: int = 900

    # Feature Flags
    ENABLE_SIGNATURE_VALIDATION: bool = True

    # Rate Limiting
    DAILY_MESSAGE_LIMIT: int = 1000
    RATE_LIMIT_EXCLUDED_PHONES: list[str] = ["+19494136310"]

    # Discord Configuration
    DISCORD_BOT_TOKEN: str = ""       # Empty = Discord disabled
    DISCORD_PUBLIC_KEY: str = ""      # Ed25519 public key from Discord Developer Portal
    DISCORD_APPLICATION_ID: str = ""  # Discord application (client) ID

    # Telegram Configuration
    TELEGRAM_BOT_TOKEN: str = ""  # Empty = Telegram disabled

    # Slack Configuration
    SLACK_BOT_TOKEN: str = ""  # Empty = Slack disabled
    SLACK_SIGNING_SECRET: str = ""

    # OAuth Configuration
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REDIRECT_URI: str = ""  # e.g., https://sms-gateway-xxx.run.app/oauth/google/callback
    OAUTH_STATE_TTL_SECONDS: int = 600  # 10 minutes
    
    # Email configuration
    EMAIL_PUBSUB_TOPIC: str = ""  # Full Pub/Sub topic name for Gmail watch
    EMAIL_DRIVE_FOLDER_ID: str = ""  # Default Drive folder for email attachments
    EMAIL_SHEET_ID: str = ""  # Default Sheets ID for email logging

    # Artifact registry
    ARTIFACT_BUCKET_NAME: str = ""  # GCS bucket for email attachment binaries; computed from project if unset

    # Scopes per feature.
    # "google"           — all personal-user scopes (calendar, drive, sheets, gmail)
    # "workspace_system" — system account (schoopet.agent@gmail.com) for team agent
    OAUTH_SCOPES: dict[str, list[str]] = {
        "google": [
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.labels",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
        "workspace_system": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
    }

    @model_validator(mode="after")
    def compute_defaults(self) -> "Settings":
        if not self.ARTIFACT_BUCKET_NAME:
            self.ARTIFACT_BUCKET_NAME = f"{self.GOOGLE_CLOUD_PROJECT}-agent-artifacts"
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore unknown environment variables


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


