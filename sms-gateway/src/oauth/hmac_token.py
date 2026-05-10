"""HMAC-signed tokens for secure OAuth initiation.

This module provides functions to generate and validate HMAC-signed tokens
for OAuth initiation. These tokens:
- Contain the user ID and expiration timestamp
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


def generate_oauth_init_token(user_id: str, secret: str) -> str:
    """Generate an HMAC-signed OAuth initiation token.

    Args:
        user_id: The user identifier.
        secret: The HMAC secret key

    Returns:
        Base64-encoded token string

    Token format (before encoding): {user_id}:{expires}:{signature}
    """
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    message = f"{user_id}:{expires}"

    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    token_data = f"{message}:{signature}"
    return base64.urlsafe_b64encode(token_data.encode()).decode()


def validate_oauth_init_token(token: str, secret: str) -> Optional[str]:
    """Validate token and return user ID if valid.

    Args:
        token: The base64-encoded token to validate
        secret: The HMAC secret key

    Returns:
        The user ID if token is valid, None otherwise

    Returns None if:
    - Token format is invalid
    - Token has expired
    - Signature doesn't match (tampered)
    """
    try:
        # Fix base64 padding if missing (common with URL-encoded tokens)
        # Base64 strings should be a multiple of 4 characters
        original_token = token
        padding_needed = 4 - (len(token) % 4)
        if padding_needed != 4:
            token = token + ("=" * padding_needed)
            logger.debug(f"Added {padding_needed} padding chars to token")

        # Decode base64
        decoded = base64.urlsafe_b64decode(token).decode()

        # Split into components (user_id may contain colons in theory, so rsplit)
        user_id, expires_str, signature = decoded.rsplit(":", 2)
        expires = int(expires_str)

        # Check expiration
        if expires < time.time():
            logger.warning(f"OAuth token expired. Expires: {expires}, Now: {time.time()}")
            return None

        # Verify signature using constant-time comparison
        message = f"{user_id}:{expires_str}"
        expected = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.warning("OAuth token signature mismatch for user %s", user_id)
            return None

        return user_id

    except Exception as e:
        logger.error(f"OAuth token validation error: {e}")
        return None
