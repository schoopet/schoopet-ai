"""Unit tests for Twilio signature validation."""
import pytest
from unittest.mock import MagicMock, patch

from src.webhook.validator import TwilioValidator


class TestTwilioValidator:
    """Tests for TwilioValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance with test token."""
        return TwilioValidator("test_auth_token_12345")

    def test_missing_signature_returns_false(self, validator):
        """Missing signature should return False."""
        result = validator.validate(
            url="https://example.com/webhook/sms",
            params={"From": "+14155551234", "Body": "test"},
            signature="",
        )
        assert result is False

    def test_none_signature_returns_false(self, validator):
        """None signature should return False."""
        result = validator.validate(
            url="https://example.com/webhook/sms",
            params={"From": "+14155551234", "Body": "test"},
            signature=None,
        )
        assert result is False

    @patch("src.webhook.validator.RequestValidator")
    def test_valid_signature_returns_true(self, mock_request_validator):
        """Valid signature should return True."""
        # Mock the RequestValidator to return True
        mock_instance = MagicMock()
        mock_instance.validate.return_value = True
        mock_request_validator.return_value = mock_instance

        validator = TwilioValidator("test_token")
        result = validator.validate(
            url="https://example.com/webhook/sms",
            params={"From": "+14155551234", "Body": "test"},
            signature="valid_signature",
        )

        assert result is True
        mock_instance.validate.assert_called_once_with(
            "https://example.com/webhook/sms",
            {"From": "+14155551234", "Body": "test"},
            "valid_signature",
        )

    @patch("src.webhook.validator.RequestValidator")
    def test_invalid_signature_returns_false(self, mock_request_validator):
        """Invalid signature should return False."""
        # Mock the RequestValidator to return False
        mock_instance = MagicMock()
        mock_instance.validate.return_value = False
        mock_request_validator.return_value = mock_instance

        validator = TwilioValidator("test_token")
        result = validator.validate(
            url="https://example.com/webhook/sms",
            params={"From": "+14155551234", "Body": "test"},
            signature="invalid_signature",
        )

        assert result is False

    @patch("src.webhook.validator.RequestValidator")
    def test_url_passed_correctly(self, mock_request_validator):
        """URL should be passed to validator correctly."""
        mock_instance = MagicMock()
        mock_instance.validate.return_value = True
        mock_request_validator.return_value = mock_instance

        validator = TwilioValidator("test_token")
        validator.validate(
            url="https://my-service.run.app/webhook/sms",
            params={"From": "+14155551234"},
            signature="sig",
        )

        call_args = mock_instance.validate.call_args
        assert call_args[0][0] == "https://my-service.run.app/webhook/sms"

    @patch("src.webhook.validator.RequestValidator")
    def test_params_passed_correctly(self, mock_request_validator):
        """Request params should be passed to validator correctly."""
        mock_instance = MagicMock()
        mock_instance.validate.return_value = True
        mock_request_validator.return_value = mock_instance

        validator = TwilioValidator("test_token")
        params = {
            "From": "+14155551234",
            "To": "+14155559999",
            "Body": "Hello",
            "MessageSid": "SM123",
        }
        validator.validate(
            url="https://example.com/webhook",
            params=params,
            signature="sig",
        )

        call_args = mock_instance.validate.call_args
        assert call_args[0][1] == params
