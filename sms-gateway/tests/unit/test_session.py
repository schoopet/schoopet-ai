"""Unit tests for session management."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.session.manager import SessionManager
from src.session.models import SessionDocument, SessionInfo


class TestSessionDocument:
    """Tests for SessionDocument model."""

    def test_to_firestore(self):
        """Should convert to Firestore-compatible dict."""
        doc = SessionDocument(
            phone_number="+14155551234",
            agent_session_id="session-123",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            last_activity=datetime(2024, 1, 1, 12, 30, 0),
            message_count=5,
        )

        data = doc.to_firestore()

        assert data["phone_number"] == "+14155551234"
        assert data["agent_session_id"] == "session-123"
        assert data["message_count"] == 5

    def test_from_firestore(self):
        """Should create instance from Firestore data."""
        data = {
            "phone_number": "+14155551234",
            "agent_session_id": "session-123",
            "created_at": datetime(2024, 1, 1, 12, 0, 0),
            "last_activity": datetime(2024, 1, 1, 12, 30, 0),
            "message_count": 5,
            "opted_in": True,
        }

        doc = SessionDocument.from_firestore(data)

        assert doc.phone_number == "+14155551234"
        assert doc.agent_session_id == "session-123"
        assert doc.message_count == 5
        assert doc.opted_in is True


class TestSessionManager:
    """Tests for SessionManager class."""

    @pytest.fixture
    def mock_firestore(self):
        """Create mock Firestore client."""
        client = AsyncMock()
        collection = MagicMock()
        document = AsyncMock()

        client.collection.return_value = collection
        collection.document.return_value = document

        return client, document

    @pytest.fixture
    def mock_agent_client(self):
        """Create mock Agent Engine client."""
        client = AsyncMock()
        client.create_session = AsyncMock(return_value="new-session-123")
        return client

    @pytest.fixture
    def session_manager(self, mock_firestore, mock_agent_client):
        """Create SessionManager with mocks."""
        client, _ = mock_firestore
        return SessionManager(
            firestore_client=client,
            agent_client=mock_agent_client,
            timeout_minutes=10,
        )

    def test_normalize_phone(self, session_manager):
        """Should normalize phone numbers for document IDs."""
        assert session_manager._normalize_phone("+14155551234") == "14155551234"
        assert session_manager._normalize_phone("+1-415-555-1234") == "14155551234"
        assert session_manager._normalize_phone("14155551234") == "14155551234"

    @pytest.mark.asyncio
    async def test_new_user_creates_session(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """First message from new number creates new session."""
        _, doc_ref = mock_firestore
        mock_doc = AsyncMock()
        mock_doc.exists = False
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session("+14155551234")

        # For a non-opted-in user, no agent session is created
        assert result.opted_in is False
        assert result.is_new_session is False
        mock_agent_client.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_activity_continues_session(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """Message within timeout continues existing session."""
        from datetime import timezone
        _, doc_ref = mock_firestore

        # Mock existing session with recent activity (opted in)
        existing_data = {
            "phone_number": "+14155551234",
            "agent_session_id": "existing-session",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "last_activity": datetime.now(timezone.utc) - timedelta(minutes=5),
            "message_count": 10,
            "opted_in": True,
        }

        mock_doc = AsyncMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = existing_data
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session("+14155551234")

        assert result.agent_session_id == "existing-session"
        assert result.is_new_session is False
        mock_agent_client.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_session_creates_new(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """Message after timeout creates new session."""
        from datetime import timezone
        _, doc_ref = mock_firestore

        # Mock existing session with stale activity (15 minutes ago, opted in)
        stale_data = {
            "phone_number": "+14155551234",
            "agent_session_id": "old-session",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "last_activity": datetime.now(timezone.utc) - timedelta(minutes=15),
            "message_count": 5,
            "opted_in": True,
        }

        mock_doc = AsyncMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = stale_data
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session("+14155551234")

        assert result.agent_session_id == "new-session-123"
        assert result.is_new_session is True
        mock_agent_client.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_last_activity(self, mock_firestore, session_manager):
        """Should update timestamp and increment message count."""
        _, doc_ref = mock_firestore

        await session_manager.update_last_activity("+14155551234")

        doc_ref.update.assert_called_once()
        call_args = doc_ref.update.call_args[0][0]
        assert "last_activity" in call_args
        assert "message_count" in call_args

    @pytest.mark.asyncio
    async def test_get_session_exists(self, mock_firestore, session_manager):
        """Should return session document if exists."""
        from datetime import timezone
        _, doc_ref = mock_firestore

        existing_data = {
            "phone_number": "+14155551234",
            "agent_session_id": "session-123",
            "created_at": datetime.now(timezone.utc),
            "last_activity": datetime.now(timezone.utc),
            "message_count": 3,
            "opted_in": True,
        }

        mock_doc = AsyncMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = existing_data
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_session("+14155551234")

        assert result is not None
        assert result.agent_session_id == "session-123"

    @pytest.mark.asyncio
    async def test_get_session_not_exists(self, mock_firestore, session_manager):
        """Should return None if session doesn't exist."""
        _, doc_ref = mock_firestore

        mock_doc = AsyncMock()
        mock_doc.exists = False
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_session("+14155551234")

        assert result is None
