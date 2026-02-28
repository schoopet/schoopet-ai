"""Slack signing secret validation."""
import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

# Maximum age in seconds for a valid Slack request
MAX_REQUEST_AGE_SECONDS = 300


class SlackValidator:
    """Validates Slack webhook requests using HMAC-SHA256 signing secrets.

    Slack signs each request using the signing secret and the request body.
    The signature is in X-Slack-Signature and the timestamp in
    X-Slack-Request-Timestamp. Requests older than 5 minutes are rejected.
    """

    def __init__(self, signing_secret: str):
        """Initialize validator with signing secret.

        Args:
            signing_secret: Slack app signing secret from the Slack API dashboard.
        """
        self._signing_secret = signing_secret

    def validate(self, timestamp: str, body: bytes, signature: str) -> bool:
        """Validate a Slack request signature.

        Args:
            timestamp: Value of X-Slack-Request-Timestamp header.
            body: Raw request body bytes.
            signature: Value of X-Slack-Signature header (format: "v0=<hex>").

        Returns:
            True if the signature is valid and request is fresh, False otherwise.
        """
        if not timestamp or not signature:
            logger.warning("Missing Slack timestamp or signature header")
            return False

        try:
            request_time = int(timestamp)
        except ValueError:
            logger.warning(f"Invalid Slack timestamp value: {timestamp!r}")
            return False

        if abs(time.time() - request_time) > MAX_REQUEST_AGE_SECONDS:
            logger.warning(f"Stale Slack request: timestamp={timestamp}")
            return False

        basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
        expected = (
            "v0="
            + hmac.new(
                self._signing_secret.encode(),
                basestring,
                hashlib.sha256,
            ).hexdigest()
        )

        is_valid = hmac.compare_digest(expected, signature)
        if not is_valid:
            logger.warning("Invalid Slack request signature")

        return is_valid
