"""Integration tests for webhook endpoint."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.channel import MessageChannel


class TestWebhookEndpoint:
    """Integration tests for /webhook/sms endpoint."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.GOOGLE_CLOUD_PROJECT = "test-project"
        settings.GOOGLE_CLOUD_LOCATION = "us-central1"
        settings.AGENT_ENGINE_ID = "test-engine"
        settings.TWILIO_ACCOUNT_SID = "AC123"
        settings.TWILIO_AUTH_TOKEN = "test_token"
        settings.TWILIO_PHONE_NUMBER = "+14155559999"
        settings.TWILIO_WHATSAPP_NUMBER = "+14155559999"
        settings.SESSION_TIMEOUT_MINUTES = 10
        settings.AGENT_TIMEOUT_SECONDS = 30
        settings.SMS_SEGMENT_DELAY_MS = 500
        settings.MAX_SMS_SEGMENTS = 10
        settings.ENABLE_SIGNATURE_VALIDATION = True
        return settings

    @pytest.fixture
    def mock_services(self):
        """Create mock services."""
        validator = MagicMock()
        validator.validate.return_value = True

        session_manager = AsyncMock()
        session_info = MagicMock()
        session_info.agent_session_id = "session-123"
        session_info.is_new_session = False
        session_manager.get_or_create_session.return_value = session_info

        agent_client = AsyncMock()
        agent_client.send_message.return_value = "Hello from the agent!"

        sms_sender = AsyncMock()
        sms_sender.send.return_value = "SM123"
        sms_sender.send_multi.return_value = ["SM123"]

        return {
            "validator": validator,
            "session_manager": session_manager,
            "agent_client": agent_client,
            "sms_sender": sms_sender,
        }

    @pytest.fixture
    def client(self, mock_settings, mock_services):
        """Create test client with mocked dependencies."""
        with patch("src.main.get_settings", return_value=mock_settings):
            with patch("src.main.firestore.AsyncClient"):
                with patch("src.main.AgentEngineClient"):
                    with patch("src.main.SessionManager"):
                        with patch("src.main.SMSSender"):
                            with patch("src.main.TwilioValidator"):
                                # Import app after patching
                                from src.main import app
                                from src.webhook import handler

                                # Initialize services manually
                                handler.init_services(
                                    validator=mock_services["validator"],
                                    session_manager=mock_services["session_manager"],
                                    agent_client=mock_services["agent_client"],
                                    sms_sender=mock_services["sms_sender"],
                                )

                                yield TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def valid_request_data(self):
        """Standard valid webhook request data."""
        return {
            "From": "+14155551234",
            "To": "+14155559999",
            "Body": "Hello Shoopet!",
            "MessageSid": "SM1234567890abcdef",
            "AccountSid": "AC123",
        }

    def test_valid_request_returns_twiml(
        self, client, valid_request_data, mock_services
    ):
        """Valid webhook request returns empty TwiML."""
        response = client.post(
            "/webhook/sms",
            data=valid_request_data,
            headers={"X-Twilio-Signature": "valid_signature"},
        )

        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "<Response></Response>" in response.text

    def test_invalid_signature_returns_400(
        self, client, valid_request_data, mock_services
    ):
        """Invalid signature returns 400 Bad Request."""
        mock_services["validator"].validate.return_value = False

        response = client.post(
            "/webhook/sms",
            data=valid_request_data,
            headers={"X-Twilio-Signature": "invalid"},
        )

        assert response.status_code == 400

    def test_missing_from_returns_400(self, client, mock_services):
        """Missing From field returns 400 Bad Request."""
        response = client.post(
            "/webhook/sms",
            data={
                "To": "+14155559999",
                "Body": "test",
                "MessageSid": "SM123",
            },
            headers={"X-Twilio-Signature": "valid"},
        )

        assert response.status_code == 400

    def test_missing_body_returns_400(self, client, mock_services):
        """Missing Body field returns 400 Bad Request."""
        response = client.post(
            "/webhook/sms",
            data={
                "From": "+14155551234",
                "To": "+14155559999",
                "MessageSid": "SM123",
            },
            headers={"X-Twilio-Signature": "valid"},
        )

        assert response.status_code == 400

    def test_empty_body_returns_400(self, client, mock_services):
        """Empty Body field returns 400 Bad Request."""
        response = client.post(
            "/webhook/sms",
            data={
                "From": "+14155551234",
                "To": "+14155559999",
                "Body": "   ",  # Whitespace only
                "MessageSid": "SM123",
            },
            headers={"X-Twilio-Signature": "valid"},
        )

        assert response.status_code == 400

    def test_health_endpoint(self, client):
        """Health check endpoint should return healthy status."""
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_root_endpoint(self, client):
        """Root endpoint should return service info."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["service"] == "Shoopet SMS Gateway"


class TestBackgroundProcessing:
    """Tests for background message processing."""

    @pytest.fixture
    def mock_services(self):
        """Create mock services for background processing tests."""
        validator = MagicMock()
        validator.validate.return_value = True

        session_manager = AsyncMock()
        # Mock get_or_create_user for opt-in check
        user_info = MagicMock()
        user_info.opted_in = True
        user_info.is_new_user = False
        session_manager.get_or_create_user.return_value = user_info
        # Mock get_or_create_session for agent session
        session_info = MagicMock()
        session_info.agent_session_id = "session-123"
        session_info.is_new_session = False
        session_manager.get_or_create_session.return_value = session_info

        agent_client = AsyncMock()
        agent_client.send_message.return_value = "Hello from the agent!"

        sms_sender = AsyncMock()
        sms_sender.send.return_value = "SM123"

        rate_limiter = AsyncMock()
        rate_limiter.check_and_increment.return_value = (True, 1)

        return {
            "validator": validator,
            "session_manager": session_manager,
            "agent_client": agent_client,
            "sms_sender": sms_sender,
            "rate_limiter": rate_limiter,
        }

    @pytest.mark.asyncio
    async def test_process_message_calls_agent(self, mock_services):
        """Background processing should call agent with message."""
        from src.webhook.handler import process_message_async, init_services

        init_services(
            validator=mock_services["validator"],
            session_manager=mock_services["session_manager"],
            agent_client=mock_services["agent_client"],
            sms_sender=mock_services["sms_sender"],
            rate_limiter=mock_services["rate_limiter"],
        )

        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.SMS,
        )

        mock_services["agent_client"].send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_message_sends_response(self, mock_services):
        """Background processing should send response message."""
        from src.webhook.handler import process_message_async, init_services

        init_services(
            validator=mock_services["validator"],
            session_manager=mock_services["session_manager"],
            agent_client=mock_services["agent_client"],
            sms_sender=mock_services["sms_sender"],
            rate_limiter=mock_services["rate_limiter"],
        )

        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.SMS,
        )

        # Verify send was called (Twilio handles splitting)
        mock_services["sms_sender"].send.assert_called()

    @pytest.mark.asyncio
    async def test_process_message_updates_session(self, mock_services):
        """Background processing should update session activity."""
        from src.webhook.handler import process_message_async, init_services

        init_services(
            validator=mock_services["validator"],
            session_manager=mock_services["session_manager"],
            agent_client=mock_services["agent_client"],
            sms_sender=mock_services["sms_sender"],
            rate_limiter=mock_services["rate_limiter"],
        )

        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.SMS,
        )

        mock_services["session_manager"].update_last_activity.assert_called_once_with(
            "+14155551234", channel="sms"
        )

    @pytest.mark.asyncio
    async def test_agent_timeout_sends_error(self, mock_services):
        """Agent timeout should send user-friendly error SMS."""
        import asyncio
        from src.webhook.handler import process_message_async, init_services

        mock_services["agent_client"].send_message.side_effect = asyncio.TimeoutError()

        init_services(
            validator=mock_services["validator"],
            session_manager=mock_services["session_manager"],
            agent_client=mock_services["agent_client"],
            sms_sender=mock_services["sms_sender"],
            rate_limiter=mock_services["rate_limiter"],
        )

        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.SMS,
        )

        # Should send error SMS
        mock_services["sms_sender"].send.assert_called_once()
        error_message = mock_services["sms_sender"].send.call_args[0][1]
        assert "taking longer" in error_message.lower()

    @pytest.mark.asyncio
    async def test_empty_response_sends_error(self, mock_services):
        """Empty agent response should send error SMS."""
        from src.webhook.handler import process_message_async, init_services

        mock_services["agent_client"].send_message.return_value = ""

        init_services(
            validator=mock_services["validator"],
            session_manager=mock_services["session_manager"],
            agent_client=mock_services["agent_client"],
            sms_sender=mock_services["sms_sender"],
            rate_limiter=mock_services["rate_limiter"],
        )

        await process_message_async(
            phone_number="+14155551234",
            message="Hello",
            message_sid="SM123",
            channel=MessageChannel.SMS,
        )

        # Should send error SMS
        mock_services["sms_sender"].send.assert_called_once()
