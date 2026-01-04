"""Twilio webhook signature validation."""
import logging
from twilio.request_validator import RequestValidator

logger = logging.getLogger(__name__)


class TwilioValidator:
    """Validates Twilio webhook request signatures."""

    def __init__(self, auth_token: str):
        """Initialize validator with Twilio auth token.

        Args:
            auth_token: Twilio account auth token for signature validation.
        """
        self._validator = RequestValidator(auth_token)

    def validate(self, url: str, params: dict, signature: str) -> bool:
        """Validate a Twilio webhook request signature.

        Args:
            url: The full URL of the webhook endpoint (including https://).
            params: Dictionary of request parameters (form data).
            signature: The X-Twilio-Signature header value.

        Returns:
            True if signature is valid, False otherwise.
        """
        if not signature:
            logger.warning("Missing X-Twilio-Signature header")
            return False

        is_valid = self._validator.validate(url, params, signature)

        if not is_valid:
            logger.warning(f"Invalid Twilio signature for URL: {url}")

        return is_valid
