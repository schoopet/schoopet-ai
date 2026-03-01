"""Unit tests for OAuthClient."""
import pytest
from unittest.mock import MagicMock, patch, ANY
from datetime import datetime, timezone, timedelta
from agents.schoopet.oauth_client import OAuthClient

# Sample data
PHONE_NUMBER = "+14155551234"
NORMALIZED_PHONE = "14155551234"
ACCESS_TOKEN = "access-token-123"
REFRESH_TOKEN = "refresh-token-456"
PROJECT_ID = "test-project"
OAUTH_BASE_URL = "https://example.com"
CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
HMAC_SECRET = "test-hmac-secret"

@pytest.fixture
def mock_env():
    """Mock environment variables."""
    with patch("os.getenv") as mock_getenv:
        def getenv_side_effect(key, default=None):
            env_vars = {
                "GOOGLE_CLOUD_PROJECT": PROJECT_ID,
                "OAUTH_BASE_URL": OAUTH_BASE_URL,
                "GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID,
                "GOOGLE_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
            }
            return env_vars.get(key, default)
        
        mock_getenv.side_effect = getenv_side_effect
        yield mock_getenv

@pytest.fixture
def oauth_client(mock_env):
    """Create an OAuthClient instance with mocked clients."""
    with patch("google.cloud.firestore.Client") as mock_firestore_cls, \
         patch("google.cloud.secretmanager.SecretManagerServiceClient") as mock_secret_cls:
        
        client = OAuthClient()
        # Trigger lazy init via _ensure_initialized (usually called by methods)
        client._ensure_initialized()
        
        # Inject mocks manually to control them
        client._firestore_client = mock_firestore_cls.return_value
        client._secret_client = mock_secret_cls.return_value
        
        # Inject HMAC secret directly to avoid Secret Manager call
        client._hmac_secret = HMAC_SECRET
        
        yield client

class TestOAuthClient:
    """Tests for OAuthClient class."""

    def test_initialization(self, oauth_client):
        """Should initialize with correct configuration."""
        assert oauth_client._project_id == PROJECT_ID
        assert oauth_client._oauth_base_url == OAUTH_BASE_URL
        assert oauth_client._client_id == CLIENT_ID
        assert oauth_client._client_secret == CLIENT_SECRET

    def test_normalize_phone(self, oauth_client):
        """Should normalize phone numbers correctly."""
        assert oauth_client._normalize_user_id("+1-415-555-1234") == "14155551234"
        assert oauth_client._normalize_user_id("14155551234") == "14155551234"

    def test_get_oauth_link(self, oauth_client):
        """Should generate correct OAuth link with feature."""
        link = oauth_client.get_oauth_link(PHONE_NUMBER, "test-feature")
        
        assert link.startswith(f"{OAUTH_BASE_URL}/oauth/google/initiate")
        assert "feature=test-feature" in link
        assert "token=" in link

    def test_get_token_data_success(self, oauth_client):
        """Should retrieve token data from Firestore."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"access_token": ACCESS_TOKEN}
        
        mock_col = oauth_client._firestore_client.collection.return_value
        mock_doc_ref = mock_col.document.return_value
        mock_doc_ref.get.return_value = mock_doc
        
        data = oauth_client.get_token_data(PHONE_NUMBER, "calendar")
        
        mock_col.document.assert_called_with(f"{NORMALIZED_PHONE}_calendar")
        assert data["access_token"] == ACCESS_TOKEN

    def test_get_valid_access_token_cached(self, oauth_client):
        """Should return cached token if valid."""
        token_data = {
            "access_token": ACCESS_TOKEN,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        
        with patch.object(oauth_client, "get_token_data", return_value=token_data):
            token = oauth_client.get_valid_access_token(PHONE_NUMBER, "calendar")
            assert token == ACCESS_TOKEN

    def test_get_valid_access_token_refresh(self, oauth_client):
        """Should refresh token if expired."""
        token_data = {
            "access_token": "expired-token",
            "expires_at": datetime.now(timezone.utc) - timedelta(minutes=5)
        }
        
        with patch.object(oauth_client, "get_token_data", return_value=token_data), \
             patch.object(oauth_client, "_get_refresh_token", return_value=REFRESH_TOKEN), \
             patch.object(oauth_client, "_refresh_access_token", return_value="new-token"):
            
            token = oauth_client.get_valid_access_token(PHONE_NUMBER, "calendar")
            assert token == "new-token"
            oauth_client._refresh_access_token.assert_called_with(PHONE_NUMBER, REFRESH_TOKEN, "calendar")

    def test_is_token_expired(self, oauth_client):
        """Should correctly identify expired tokens."""
        now = datetime.now(timezone.utc)
        
        # Valid
        valid_data = {"expires_at": now + timedelta(minutes=10)}
        assert not oauth_client.is_token_expired(valid_data)
        
        # Expired (within buffer)
        expired_buffer = {"expires_at": now + timedelta(seconds=30)}
        assert oauth_client.is_token_expired(expired_buffer)
        
        # Expired (past)
        expired_past = {"expires_at": now - timedelta(minutes=10)}
        assert oauth_client.is_token_expired(expired_past)

    def test_get_secret_fallback(self):
        """Should fetch secret from Secret Manager if missing from env."""
        def getenv_side_effect(key, default=None):
            if key == "GOOGLE_CLOUD_PROJECT":
                return PROJECT_ID
            return None # Return None for secrets to trigger fallback

        with patch("os.getenv", side_effect=getenv_side_effect), \
             patch("google.cloud.secretmanager.SecretManagerServiceClient") as mock_secret_cls:
            
            # Setup mock response
            mock_client = mock_secret_cls.return_value
            mock_response = MagicMock()
            mock_response.payload.data.decode.return_value = "secret-value"
            mock_client.access_secret_version.return_value = mock_response
            
            client = OAuthClient()
            
            # This triggers _ensure_initialized, which sets project_id but leaves client_id/secret None
            val = client._get_secret("test-secret")
            
            assert val == "secret-value"
            # Verify it tried to fetch the secret
            mock_client.access_secret_version.assert_called()
