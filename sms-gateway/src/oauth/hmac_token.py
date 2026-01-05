"""HMAC-signed tokens for secure OAuth initiation.

This module provides functions to generate and validate HMAC-signed tokens
for OAuth initiation. These tokens:
- Contain the phone number and expiration timestamp
- Are cryptographically signed to prevent tampering
- Expire after 10 minutes
- Are stateless (no database storage required)
"""
import hmac
import hashlib
import base64
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 600  # 10 minutes


def generate_oauth_init_token(phone_number: str, secret: str) -> str:
    """Generate an HMAC-signed OAuth initiation token.

    Args:
        phone_number: The user's phone number in E.164 format
        secret: The HMAC secret key

    Returns:
        Base64-encoded token string

    Token format (before encoding): {phone}:{expires}:{signature}
    """
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    message = f"{phone_number}:{expires}"

    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    token_data = f"{message}:{signature}"
    return base64.urlsafe_b64encode(token_data.encode()).decode()


def validate_oauth_init_token(token: str, secret: str) -> Optional[str]:
    """Validate token and return phone number if valid.

    Args:
        token: The base64-encoded token to validate
        secret: The HMAC secret key

    Returns:
        The phone number if token is valid, None otherwise

    Returns None if:
    - Token format is invalid
    - Token has expired
    - Signature doesn't match (tampered)
    """
    try:
        # Decode base64
        decoded = base64.urlsafe_b64decode(token).decode()

        # Split into components (phone may contain colons in theory, so rsplit)
        phone, expires_str, signature = decoded.rsplit(":", 2)
        expires = int(expires_str)

        # Check expiration
        if expires < time.time():
            logger.warning(f"OAuth token expired. Expires: {expires}, Now: {time.time()}")
            return None

        # Verify signature using constant-time comparison
        message = f"{phone}:{expires_str}"
        expected = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.warning(
                f"OAuth token signature mismatch. Phone: {phone}. "
                f"Expected: {expected[:8]}..., Got: {signature[:8]}..."
            )
            return None

        return phone

    except Exception as e:
        logger.error(f"OAuth token validation error: {e}")
        return None
