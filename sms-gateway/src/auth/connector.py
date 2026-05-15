"""IAM Connector token helper for background (non-ADK-session) contexts."""
import asyncio
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


_FINALIZE_BASE = "https://iamconnectorcredentials.googleapis.com/v1alpha"


async def finalize_iam_credentials(
    connector_name: str,
    user_id: str,
    consent_nonce: str,
    user_id_validation_state: str,
) -> None:
    """Call credentials:finalize on the IAM connector REST API.

    This is required after the user completes the OAuth consent flow.
    Without this call the token is never stored in the IAM connector backend
    and subsequent get_auth_credential calls will keep returning None.

    Args:
        connector_name: Full connector resource name
            (e.g. projects/P/locations/L/connectors/C).
        user_id: The user ID passed to the Agent Engine session.
        consent_nonce: The nonce from the adk_request_credential event.
        user_id_validation_state: Opaque state from the IAM connector callback.
    """
    import google.auth
    import google.auth.transport.requests
    import httpx

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    req = google.auth.transport.requests.Request()
    await asyncio.get_running_loop().run_in_executor(None, credentials.refresh, req)

    finalize_url = f"{_FINALIZE_BASE}/{connector_name}/credentials:finalize"
    payload = {
        "userId": user_id,
        "userIdValidationState": user_id_validation_state,
        "consentNonce": consent_nonce,
    }
    logger.info(
        f"Calling credentials:finalize for user {user_id[:4]}****, "
        f"connector={connector_name.split('/')[-1]}"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            finalize_url,
            json=payload,
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        if not resp.is_success:
            logger.error(
                f"credentials:finalize failed: status={resp.status_code} body={resp.text[:200]}"
            )
        resp.raise_for_status()
    logger.info(f"credentials:finalize succeeded for user {user_id[:4]}****")


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
