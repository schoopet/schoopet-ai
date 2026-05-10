"""Tests for SMS gateway configuration defaults."""
from src.config import Settings


def test_google_oauth_scopes_include_docs_writes():
    settings = Settings(GOOGLE_CLOUD_PROJECT="test-project")

    assert "https://www.googleapis.com/auth/documents" in settings.OAUTH_SCOPES["google"]
