"""GCP Agent Identity auth provider — registers GcpAuthProvider at import time."""
import logging
import os
from datetime import datetime, timezone

from google.adk.auth.credential_manager import CredentialManager
from google.adk.auth.auth_tool import AuthConfig
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme

logger = logging.getLogger(__name__)

_provider = GcpAuthProvider()
CredentialManager.register_auth_provider(_provider)

GOOGLE_PERSONAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def build_auth_config() -> AuthConfig:
    return AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", ""),
            scopes=GOOGLE_PERSONAL_SCOPES,
            continue_uri=os.getenv("IAM_CONNECTOR_CONTINUE_URI") or None,
        )
    )


def get_credential_manager() -> CredentialManager:
    return CredentialManager(auth_config=build_auth_config())


def extract_and_validate_token(credential, tool_name: str) -> str | None:
    """Extract the OAuth token from an ADK credential and validate it.

    Returns the token string if it looks usable, or None if it is missing.
    Logs a WARNING for any condition that is likely to cause a silent API
    failure downstream (empty token, expired token).

    Args:
        credential: ADK AuthCredential returned by CredentialManager.get_auth_credential().
        tool_name: Short label used in log messages (e.g. "calendar", "gmail").

    Returns:
        The access token string, or None when the token is absent.
    """
    tag = f"[token-validate:{tool_name}]"
    try:
        http_creds = credential.http.credentials
        token: str | None = http_creds.token
    except AttributeError as e:
        logger.warning(f"{tag} unexpected credential structure: {e}")
        return None

    if not token:
        logger.warning(
            f"{tag} token is empty — IAM connector returned a credential with no access token; "
            f"the stored credential may be corrupted or not yet fully propagated"
        )
        return None

    token_prefix = token[:8] if len(token) >= 8 else token

    expiry: datetime | None = getattr(http_creds, "expiry", None)
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if expiry <= now:
            seconds_ago = (now - expiry).total_seconds()
            logger.warning(
                f"{tag} token expired {seconds_ago:.0f}s ago "
                f"(expiry={expiry.isoformat()}, prefix={token_prefix}...)"
            )
        else:
            seconds_left = (expiry - now).total_seconds()
            logger.info(
                f"{tag} token valid for {seconds_left:.0f}s "
                f"(prefix={token_prefix}...)"
            )
    else:
        logger.info(f"{tag} token present, no expiry info (prefix={token_prefix}...)")

    return token
