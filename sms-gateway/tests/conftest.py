"""Pytest configuration and fixtures."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_firestore_client():
    """Create a mock Firestore async client."""
    client = AsyncMock()
    collection = AsyncMock()
    document = AsyncMock()

    client.collection.return_value = collection
    collection.document.return_value = document

    return client


@pytest.fixture
def mock_agent_client():
    """Create a mock Agent Engine client."""
    client = AsyncMock()
    client.create_session = AsyncMock(return_value="test-session-id")
    client.send_message = AsyncMock(return_value="Hello from agent!")
    return client


@pytest.fixture
def mock_twilio_client():
    """Create a mock Twilio client."""
    client = MagicMock()
    message = MagicMock()
    message.sid = "SM1234567890"
    message.status = "queued"
    client.messages.create.return_value = message
    return client
