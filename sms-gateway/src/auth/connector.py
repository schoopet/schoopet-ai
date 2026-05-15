"""IAM Connector token helper for background (non-ADK-session) contexts."""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

_provider = None
_scheme = None


def _get_provider_and_scheme():
    global _provider, _scheme
    if _provider is None:
        from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
        _provider = GcpAuthProvider()
        _scheme = GcpAuthProviderScheme(
            name=os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", ""),
            scopes=_SCOPES,
            continue_uri=os.getenv("IAM_CONNECTOR_CONTINUE_URI") or None,
        )
    return _provider, _scheme


async def get_connector_token(user_id: str) -> Optional[str]:
    """Return a stored IAM connector access token for user_id without an ADK session.

    Returns the token string if the user has already authorized, or None if
    consent is still required or retrieval fails.
    """
    provider, scheme = _get_provider_and_scheme()
    try:
        operation = await provider._retrieve_credentials(user_id, scheme)
    except Exception as e:
        logger.warning(f"IAM connector retrieval failed for {user_id[:4]}****: {e}")
        return None

    response, metadata = provider._unpack_operation(operation)

    if operation.HasField("error"):
        logger.warning(f"IAM connector error for {user_id[:4]}****: {operation.error.message}")
        return None

    if not operation.done:
        logger.info(f"No stored token for {user_id[:4]}**** (consent required or pending)")
        return None

    try:
        from google.adk.integrations.agent_identity.gcp_auth_provider import _construct_auth_credential
        credential = _construct_auth_credential(response)
        return credential.http.credentials.token
    except Exception as e:
        logger.warning(f"Failed to extract token for {user_id[:4]}****: {e}")
        return None
