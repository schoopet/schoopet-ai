"""Shared pytest fixtures for schoopet agent tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from google.adk.artifacts import InMemoryArtifactService
from google.adk.tools import ToolContext

PHONE_NUMBER = "+14155551234"
SESSION_ID = "sess-conftest"


@pytest.fixture
def tool_context():
    """Mock ToolContext with a default phone number as user_id."""
    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = PHONE_NUMBER
    return ctx


@pytest.fixture
def artifact_service():
    """In-memory artifact service (empty, ready to populate)."""
    return InMemoryArtifactService()
