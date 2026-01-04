"""Configuration settings for SMS Gateway."""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Google Cloud Configuration
    GOOGLE_CLOUD_PROJECT: str
    GOOGLE_CLOUD_LOCATION: str = "us-central1"
    AGENT_ENGINE_ID: str

    # Twilio Configuration
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str

    # Service Configuration
    SESSION_TIMEOUT_MINUTES: int = 10
    MAX_SMS_SEGMENTS: int = 10
    AGENT_TIMEOUT_SECONDS: int = 30
    SMS_SEGMENT_DELAY_MS: int = 500

    # Feature Flags
    ENABLE_SIGNATURE_VALIDATION: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
