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

    # Rate Limiting
    DAILY_MESSAGE_LIMIT: int = 1000
    RATE_LIMIT_EXCLUDED_PHONES: list[str] = ["+19494136310"]

    # OAuth Configuration
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REDIRECT_URI: str = ""  # e.g., https://sms-gateway-xxx.run.app/oauth/google/callback
    OAUTH_STATE_TTL_SECONDS: int = 600  # 10 minutes
    
    # Scopes per feature
    OAUTH_SCOPES: dict[str, list[str]] = {
        "calendar": [
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
        "house": [
            "https://www.googleapis.com/auth/sdm.service",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ]
    }

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
