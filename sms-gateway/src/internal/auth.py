"""Authentication for internal service-to-service endpoints.

This module provides security for internal endpoints that are called by:
- Task Worker (async task completion notifications)
- Cloud Tasks (scheduled task triggers)

Supports two authentication methods:
1. OIDC tokens (recommended for GCP services)
2. HMAC signatures (fallback for non-GCP services)

Security measures:
- Service account allowlist
- Timestamp validation (5-minute window) for replay attack prevention
- Constant-time comparison for timing attack prevention
"""
import asyncio
import functools
import hashlib
import hmac
import logging
import os
import time
from typing import Optional

from fastapi import HTTPException, Header, Request

logger = logging.getLogger(__name__)

# Service accounts allowed to call internal endpoints
# Loaded from environment or configured here
ALLOWED_SERVICE_ACCOUNTS: list[str] = []

# HMAC secret (loaded from Secret Manager on startup)
_internal_hmac_secret: Optional[str] = None

# Timestamp tolerance for replay attack prevention (5 minutes)
TIMESTAMP_TOLERANCE_SECONDS = 300


def init_allowed_service_accounts():
    """Initialize the list of allowed service accounts from environment."""
    global ALLOWED_SERVICE_ACCOUNTS

    # Load from environment (comma-separated)
    env_accounts = os.getenv("ALLOWED_SERVICE_ACCOUNTS", "")
    if env_accounts:
        ALLOWED_SERVICE_ACCOUNTS = [sa.strip() for sa in env_accounts.split(",") if sa.strip()]

    # Add default service accounts if project is known
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if project_id:
        default_accounts = [
            f"task-worker@{project_id}.iam.gserviceaccount.com",
        ]
        for account in default_accounts:
            if account not in ALLOWED_SERVICE_ACCOUNTS:
                ALLOWED_SERVICE_ACCOUNTS.append(account)

    logger.info(f"Initialized {len(ALLOWED_SERVICE_ACCOUNTS)} allowed service accounts")


def set_internal_hmac_secret(secret: str):
    """Set the HMAC secret (called during app startup)."""
    global _internal_hmac_secret
    _internal_hmac_secret = secret
    logger.info("Internal HMAC secret initialized")


def get_internal_hmac_secret() -> Optional[str]:
    """Get the HMAC secret for signing/verifying internal requests."""
    return _internal_hmac_secret


async def verify_oidc_token(authorization: str) -> str:
    """Verify OIDC token from Authorization header.

    Args:
        authorization: The full Authorization header value ("Bearer <token>")

    Returns:
        The authenticated service account email

    Raises:
        HTTPException: If token is invalid or service account not allowed
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = authorization[7:]  # Remove "Bearer " prefix

    try:
        # Import here to avoid startup issues if not using OIDC
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        # Verify the token
        # The audience should be the URL of this service
        audience = os.getenv("SMS_GATEWAY_URL", "")

        loop = asyncio.get_running_loop()
        claims = await loop.run_in_executor(
            None,
            functools.partial(
                id_token.verify_oauth2_token,
                token,
                google_requests.Request(),
                audience=audience if audience else None,
            ),
        )

        email = claims.get("email")
        if not email:
            logger.warning("OIDC token missing email claim")
            raise HTTPException(status_code=401, detail="Invalid token: missing email")

        # Check if service account is allowed
        if email not in ALLOWED_SERVICE_ACCOUNTS:
            logger.warning(f"Unauthorized service account: {email}")
            raise HTTPException(status_code=403, detail="Unauthorized service account")

        logger.debug(f"OIDC authentication successful: {email}")
        return email

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OIDC token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid OIDC token")


async def verify_hmac_signature(
    signature: str,
    timestamp: str,
    body: bytes,
) -> str:
    """Verify HMAC-SHA256 signature for internal requests.

    Args:
        signature: The X-Internal-Signature header value
        timestamp: The X-Internal-Timestamp header value
        body: The raw request body

    Returns:
        "hmac-authenticated" if valid

    Raises:
        HTTPException: If signature is invalid or timestamp expired
    """
    secret = get_internal_hmac_secret()
    if not secret:
        logger.error("HMAC secret not configured")
        raise HTTPException(status_code=500, detail="Internal authentication not configured")

    # Validate timestamp to prevent replay attacks
    try:
        request_time = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp format")

    current_time = int(time.time())
    time_diff = abs(current_time - request_time)

    if time_diff > TIMESTAMP_TOLERANCE_SECONDS:
        logger.warning(f"Request timestamp expired. Diff: {time_diff}s, Max: {TIMESTAMP_TOLERANCE_SECONDS}s")
        raise HTTPException(status_code=401, detail="Request timestamp expired")

    # Compute expected signature
    # Format: {timestamp}.{body}
    try:
        body_str = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Invalid request body encoding")

    message = f"{timestamp}.{body_str}"
    expected_signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(signature, expected_signature):
        logger.warning("HMAC signature mismatch")
        raise HTTPException(status_code=401, detail="Invalid signature")

    logger.debug("HMAC authentication successful")
    return "hmac-authenticated"


async def verify_internal_request(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_internal_signature: Optional[str] = Header(None, alias="X-Internal-Signature"),
    x_internal_timestamp: Optional[str] = Header(None, alias="X-Internal-Timestamp"),
) -> str:
    """Verify that a request comes from an authorized internal service.

    This is a FastAPI dependency that can be used to secure internal endpoints.
    Supports two authentication methods:
    1. OIDC token in Authorization header (preferred for Cloud Tasks)
    2. HMAC signature in X-Internal-Signature header (fallback)

    Args:
        request: The FastAPI request object
        authorization: The Authorization header (optional)
        x_internal_signature: The X-Internal-Signature header (optional)
        x_internal_timestamp: The X-Internal-Timestamp header (optional)

    Returns:
        The authenticated identity (service account email or "hmac-authenticated")

    Raises:
        HTTPException: If authentication fails
    """
    # Method 1: OIDC Token (preferred for Cloud Tasks)
    if authorization and authorization.startswith("Bearer "):
        return await verify_oidc_token(authorization)

    # Method 2: HMAC Signature (fallback)
    if x_internal_signature and x_internal_timestamp:
        # Get raw body for signature verification
        body = await request.body()
        return await verify_hmac_signature(
            signature=x_internal_signature,
            timestamp=x_internal_timestamp,
            body=body,
        )

    # No valid authentication provided
    logger.warning("No authentication provided for internal request")
    raise HTTPException(status_code=401, detail="Missing authentication")
