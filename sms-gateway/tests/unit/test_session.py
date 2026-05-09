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
            personal_agent_session_id="session-123",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            last_activity=datetime(2024, 1, 1, 12, 30, 0),
            message_count=5,
        )

        data = doc.to_firestore()

        assert data["phone_number"] == "+14155551234"
        assert data["personal_agent_session_id"] == "session-123"
        assert data["message_count"] == 5

    def test_from_firestore(self):
        """Should create instance from Firestore data."""
        data = {
            "phone_number": "+14155551234",
            "personal_agent_session_id": "session-123",
            "created_at": datetime(2024, 1, 1, 12, 0, 0),
            "last_activity": datetime(2024, 1, 1, 12, 30, 0),
            "message_count": 5,
            "opted_in": True,
        }

        doc = SessionDocument.from_firestore(data)

        assert doc.phone_number == "+14155551234"
        assert doc.personal_agent_session_id == "session-123"
        assert doc.message_count == 5
        assert doc.opted_in is True


class TestSessionManager:
    """Tests for SessionManager class."""

    @pytest.fixture
    def mock_firestore(self):
        """Create mock Firestore client."""
        client = MagicMock()
        collection = MagicMock()
        document = MagicMock()

        # document.get is awaited, so it must return a coroutine that resolves to a mock_doc
        # We use AsyncMock for the method itself.
        document.get = AsyncMock()
        document.set = AsyncMock()
        document.update = AsyncMock()

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

    def test_normalize_user_id(self):
        """Should normalize user IDs for document IDs."""
        from src.utils import normalize_user_id

        assert normalize_user_id("+14155551234") == "14155551234"
        assert normalize_user_id("+1-415-555-1234") == "14155551234"
        assert normalize_user_id("14155551234") == "14155551234"

    @pytest.mark.asyncio
    async def test_new_user_creates_session(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """First message from new number creates new session."""
        _, doc_ref = mock_firestore

        # mock_doc should be a MagicMock (synchronous methods like to_dict)
        mock_doc = MagicMock()
        mock_doc.exists = False

        # doc_ref.get is an AsyncMock, so we set its return_value to the mock_doc
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session("+14155551234", channel="sms")

        # The session manager creates a new session with opted_in=True
        assert result.opted_in is True
        assert result.is_new_session is True
        mock_agent_client.create_session.assert_called_once_with(
            user_id="+14155551234", state={"channel": "sms"}
        )

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
            "personal_agent_session_id": "existing-session",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "last_activity": datetime.now(timezone.utc) - timedelta(minutes=5),
            "message_count": 10,
            "opted_in": True,
        }

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = existing_data
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session("+14155551234", channel="sms")

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
            "personal_agent_session_id": "old-session",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "last_activity": datetime.now(timezone.utc) - timedelta(minutes=15),
            "message_count": 5,
            "opted_in": True,
        }

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = stale_data
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session("+14155551234", channel="sms")

        assert result.agent_session_id == "new-session-123"
        assert result.is_new_session is True
        mock_agent_client.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_last_activity(self, mock_firestore, session_manager):
        """Should update timestamp and increment message count."""
        _, doc_ref = mock_firestore

        await session_manager.update_last_activity("+14155551234", channel="sms")

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
            "personal_agent_session_id": "session-123",
            "created_at": datetime.now(timezone.utc),
            "last_activity": datetime.now(timezone.utc),
            "message_count": 3,
            "opted_in": True,
        }

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = existing_data
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_session("+14155551234")

        assert result is not None
        assert result.personal_agent_session_id == "session-123"

    @pytest.mark.asyncio
    async def test_pending_confirmation_round_trip(self, mock_firestore, session_manager):
        """Should store, read, and clear pending confirmation state."""
        from datetime import timezone
        _, doc_ref = mock_firestore

        confirmation = MagicMock()
        confirmation.to_firestore.return_value = {
            "function_call_id": "confirm-1",
            "original_function_call": {
                "id": "tool-1",
                "name": "send_email",
                "args": {"to": "x@example.com"},
            },
            "original_function_call_id": "tool-1",
            "tool_name": "send_email",
            "tool_args": {"to": "x@example.com"},
            "hint": "Send email?",
            "payload": {"kind": "email"},
        }

        pending = await session_manager.set_pending_confirmation(
            user_id="+14155551234",
            confirmation=confirmation,
            session_id="session-123",
            channel="discord",
        )

        assert pending["id"]
        assert pending["adk_confirmation_function_call_id"] == "confirm-1"
        assert pending["tool_name"] == "send_email"
        doc_ref.update.assert_called()

        existing_data = {
            "phone_number": "+14155551234",
            "personal_agent_session_id": "session-123",
            "created_at": datetime.now(timezone.utc),
            "last_activity": datetime.now(timezone.utc),
            "message_count": 3,
            "opted_in": True,
            "pending_confirmations": [pending],
        }
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = existing_data
        doc_ref.get.return_value = mock_doc

        assert await session_manager.get_pending_confirmation(
            "+14155551234", pending["id"]
        ) == pending
        assert await session_manager.get_pending_confirmation(
            "+14155551234", "wrong-id"
        ) is None

        await session_manager.clear_pending_confirmation("+14155551234", pending["id"])
        assert doc_ref.update.call_args[0][0] == {"pending_confirmations": []}

    @pytest.mark.asyncio
    async def test_multiple_pending_confirmations(self, mock_firestore, session_manager):
        """Should append and selectively clear individual pending confirmations."""
        from datetime import timezone
        _, doc_ref = mock_firestore

        def _make_confirmation(name: str, call_id: str):
            c = MagicMock()
            c.to_firestore.return_value = {
                "function_call_id": call_id,
                "original_function_call": {"id": call_id, "name": name, "args": {}},
                "original_function_call_id": call_id,
                "tool_name": name,
                "tool_args": {},
                "hint": "",
                "payload": None,
            }
            return c

        pending_a = await session_manager.set_pending_confirmation(
            user_id="+14155551234",
            confirmation=_make_confirmation("create_event", "cid-a"),
            session_id="session-123",
            channel="discord",
        )
        pending_b = await session_manager.set_pending_confirmation(
            user_id="+14155551234",
            confirmation=_make_confirmation("save_file", "cid-b"),
            session_id="session-123",
            channel="discord",
        )

        assert pending_a["tool_name"] == "create_event"
        assert pending_b["tool_name"] == "save_file"
        assert doc_ref.update.call_count >= 2

        # Simulate Firestore state with both pending
        existing_data = {
            "phone_number": "+14155551234",
            "personal_agent_session_id": "session-123",
            "created_at": datetime.now(timezone.utc),
            "last_activity": datetime.now(timezone.utc),
            "message_count": 1,
            "opted_in": True,
            "pending_confirmations": [pending_a, pending_b],
        }
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = existing_data
        doc_ref.get.return_value = mock_doc

        # Clearing pending_a leaves only pending_b
        await session_manager.clear_pending_confirmation("+14155551234", pending_a["id"])
        updated = doc_ref.update.call_args[0][0]["pending_confirmations"]
        assert len(updated) == 1
        assert updated[0]["id"] == pending_b["id"]

    @pytest.mark.asyncio
    async def test_get_session_not_exists(self, mock_firestore, session_manager):
        """Should return None if session doesn't exist."""
        _, doc_ref = mock_firestore

        mock_doc = MagicMock()
        mock_doc.exists = False
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_session("+14155551234")

        assert result is None

    @pytest.mark.asyncio
    async def test_new_session_passes_channel_in_state(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """Should pass channel in session state when creating a new session."""
        _, doc_ref = mock_firestore

        mock_doc = MagicMock()
        mock_doc.exists = False
        doc_ref.get.return_value = mock_doc

        await session_manager.get_or_create_session(
            "+14155551234", channel="discord"
        )

        mock_agent_client.create_session.assert_called_once_with(
            user_id="+14155551234", state={"channel": "discord"}
        )

    @pytest.mark.asyncio
    async def test_scoped_discord_session_uses_distinct_doc_and_state(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """Scoped sessions keep user_id shared while separating Firestore docs."""
        client, doc_ref = mock_firestore
        mock_doc = MagicMock()
        mock_doc.exists = False
        doc_ref.get.return_value = mock_doc

        result = await session_manager.get_or_create_session(
            "user-123",
            channel="discord",
            session_scope="discord:guild:g1:channel:c1",
            state_extra={
                "discord_guild_id": "g1",
                "discord_channel_id": "c1",
                "discord_channel_name": "project-alpha",
            },
        )

        assert result.session_scope == "discord:guild:g1:channel:c1"
        assert result.channel == "discord"
        doc_id = client.collection.return_value.document.call_args.args[0]
        assert doc_id.startswith("user123__")
        mock_agent_client.create_session.assert_called_once_with(
            user_id="user-123",
            state={
                "channel": "discord",
                "session_scope": "discord:guild:g1:channel:c1",
                "discord_guild_id": "g1",
                "discord_channel_id": "c1",
                "discord_channel_name": "project-alpha",
            },
        )

    @pytest.mark.asyncio
    async def test_opt_in_passes_channel_in_state(
        self, mock_firestore, mock_agent_client, session_manager
    ):
        """Should pass channel in session state when opting in."""
        from datetime import timezone
        _, doc_ref = mock_firestore

        # Create a pre-existing non-opted-in user
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "phone_number": "+14155551234",
            "personal_agent_session_id": "",
            "created_at": datetime.now(timezone.utc),
            "last_activity": datetime.now(timezone.utc),
            "message_count": 0,
            "opted_in": False,
        }
        doc_ref.get.return_value = mock_doc

        await session_manager.set_opted_in(
            "+14155551234", channel="telegram"
        )

        mock_agent_client.create_session.assert_called_once_with(
            user_id="+14155551234", state={"channel": "telegram"}
        )
