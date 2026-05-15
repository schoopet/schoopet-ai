"""Configuration settings for SMS Gateway."""
from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

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

    # Email configuration
    EMAIL_PUBSUB_TOPIC: str = ""  # Full Pub/Sub topic name for Gmail watch
    EMAIL_DRIVE_FOLDER_ID: str = ""  # Default Drive folder for email attachments
    EMAIL_SHEET_ID: str = ""  # Default Sheets ID for email logging

    # Artifact registry
    ARTIFACT_BUCKET_NAME: str = ""  # GCS bucket for email attachment binaries; computed from project if unset

    # IAM Connector auth (agent-identity migration)
    IAM_CONNECTOR_GOOGLE_PERSONAL_NAME: str = ""  # projects/{proj}/locations/global/connectors/{id}
    IAM_CONNECTOR_CONTINUE_URI: str = ""  # https://{gateway_url}/oauth/connector/callback

    # Scopes per feature.
    # "google" — all personal-user scopes (calendar, drive, docs, sheets, gmail)
    OAUTH_SCOPES: dict[str, list[str]] = {
        "google": [
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.labels",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
    }

    @model_validator(mode="after")
    def compute_defaults(self) -> "Settings":
        if not self.ARTIFACT_BUCKET_NAME:
            self.ARTIFACT_BUCKET_NAME = f"{self.GOOGLE_CLOUD_PROJECT}-agent-artifacts"
        return self


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
