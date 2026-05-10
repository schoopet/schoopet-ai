"""Authentication for internal service-to-service endpoints.

This module provides security for internal endpoints that are called by:
- Cloud Tasks (background task execution)
- Cloud Scheduler (scheduled maintenance)

All callers are GCP services and authenticate via OIDC tokens issued for
their service accounts. The allowed service account list is auto-populated
from GOOGLE_CLOUD_PROJECT at startup.
"""
import asyncio
import functools
import logging
import os
from typing import Optional

from fastapi import HTTPException, Header, Request

logger = logging.getLogger(__name__)

# Service accounts allowed to call internal endpoints
ALLOWED_SERVICE_ACCOUNTS: list[str] = []


def init_allowed_service_accounts():
    """Initialize the list of allowed service accounts from environment."""
    global ALLOWED_SERVICE_ACCOUNTS

    env_accounts = os.getenv("ALLOWED_SERVICE_ACCOUNTS", "")
    if env_accounts:
        ALLOWED_SERVICE_ACCOUNTS = [sa.strip() for sa in env_accounts.split(",") if sa.strip()]

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if project_id:
        default_accounts = [
            f"schoopet-sms-gateway@{project_id}.iam.gserviceaccount.com",
            f"gmail-watch-scheduler@{project_id}.iam.gserviceaccount.com",
            f"task-requeue-scheduler@{project_id}.iam.gserviceaccount.com",
        ]
        for account in default_accounts:
            if account not in ALLOWED_SERVICE_ACCOUNTS:
                ALLOWED_SERVICE_ACCOUNTS.append(account)

    logger.info(f"Initialized {len(ALLOWED_SERVICE_ACCOUNTS)} allowed service accounts")


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

    token = authorization[7:]

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        audience = os.getenv("SMS_GATEWAY_URL", "")
        if not audience:
            logger.error("SMS_GATEWAY_URL not set — cannot validate OIDC audience")
            raise HTTPException(status_code=503, detail="Server misconfigured: missing audience")

        loop = asyncio.get_running_loop()
        claims = await loop.run_in_executor(
            None,
            functools.partial(
                id_token.verify_oauth2_token,
                token,
                google_requests.Request(),
                audience=audience,
            ),
        )

        email = claims.get("email")
        if not email:
            logger.warning("OIDC token missing email claim")
            raise HTTPException(status_code=401, detail="Invalid token: missing email")

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


async def verify_internal_request(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> str:
    """Verify that a request comes from an authorized internal service.

    FastAPI dependency used to secure internal endpoints. Requires a valid
    OIDC Bearer token issued for an allowed GCP service account.

    Args:
        request: The FastAPI request object
        authorization: The Authorization header

    Returns:
        The authenticated service account email

    Raises:
        HTTPException: If authentication fails
    """
    if authorization and authorization.startswith("Bearer "):
        return await verify_oidc_token(authorization)

    logger.warning("No authentication provided for internal request")
    raise HTTPException(status_code=401, detail="Missing authentication")
