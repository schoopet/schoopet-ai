"""GCP Agent Identity auth provider — registers GcpAuthProvider at import time."""
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

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


def build_auth_config(user_id: str = "") -> AuthConfig:
    base_uri = os.getenv("IAM_CONNECTOR_CONTINUE_URI", "")
    if user_id and base_uri:
        continue_uri = f"{base_uri}?uid={quote(user_id, safe='')}"
    else:
        continue_uri = base_uri or None
    return AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", ""),
            scopes=GOOGLE_PERSONAL_SCOPES,
            continue_uri=continue_uri,
        )
    )


def extract_and_validate_token(credential, tool_name: str) -> str | None:
    """Extract the OAuth token from an ADK credential and validate it.

    Returns the token string if it looks usable, or None if it is missing.
    Logs details at every step so token lifecycle is visible in Cloud Logging.

    Args:
        credential: ADK AuthCredential returned by CredentialManager.get_auth_credential().
        tool_name: Short label used in log messages (e.g. "calendar", "gmail").

    Returns:
        The access token string, or None when the token is absent.
    """
    tag = f"[iam-token:{tool_name}]"

    # Log raw credential structure — helps detect unexpected types from ADK.
    auth_type = getattr(credential, "auth_type", "<unknown>")
    has_http = getattr(credential, "http", None) is not None
    logger.debug(f"{tag} credential received: auth_type={auth_type}, has_http={has_http}")

    try:
        http_creds = credential.http.credentials
        token: str | None = http_creds.token
    except AttributeError as e:
        logger.warning(f"{tag} unexpected credential structure: {e!r} — credential={credential!r}")
        return None

    if not token:
        logger.warning(
            f"{tag} token is empty — IAM connector returned a credential with no access token; "
            f"the stored credential may be corrupted or not yet fully propagated. "
            f"NOTE: force_refresh=False is hardcoded in GcpAuthProvider._retrieve_credentials; "
            f"the IAM connector may be returning a cached empty credential."
        )
        return None

    token_prefix = token[:8] if len(token) >= 8 else token

    # The ADK's _construct_auth_credential does NOT propagate expire_time from
    # RetrieveCredentialsResponse into HttpCredentials.expiry, so this is always
    # None for IAM connector tokens. Logged explicitly so this is visible in bug reports.
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
                f"(expiry={expiry.isoformat()}, prefix={token_prefix}...)"
            )
    else:
        logger.info(
            f"{tag} token present, expiry NOT populated by ADK (IAM connector expire_time "
            f"is stripped by _construct_auth_credential) — prefix={token_prefix}..."
        )

    return token


async def get_workspace_service(api_name: str, version: str, tool_name: str, tool_context):
    """Retrieve an authenticated Google API service client via the IAM connector.

    Calls GcpAuthProvider → IAM connector RetrieveCredentials (force_refresh=False)
    on every invocation. Logs every step so the full credential flow is visible in
    Cloud Logging for debugging token lifecycle issues.

    Returns a googleapiclient service, or None after emitting a credential request.
    """
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    connector = os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", "<IAM_CONNECTOR_GOOGLE_PERSONAL_NAME unset>")
    continue_uri = os.getenv("IAM_CONNECTOR_CONTINUE_URI", "<IAM_CONNECTOR_CONTINUE_URI unset>")
    user_id: str = getattr(tool_context, "user_id", "") or ""
    uid_tag = (user_id[:4] + "****") if len(user_id) >= 4 else user_id

    logger.info(
        f"[iam-flow:{tool_name}] get_auth_credential → IAM connector "
        f"connector={connector!r} user={uid_tag} api={api_name}/{version} "
        f"scopes={GOOGLE_PERSONAL_SCOPES} continue_uri={continue_uri!r} force_refresh=False"
    )

    cred_mgr = CredentialManager(auth_config=build_auth_config(user_id))
    try:
        credential = await cred_mgr.get_auth_credential(tool_context)
    except Exception as exc:
        logger.warning(
            f"[iam-flow:{tool_name}] get_auth_credential raised {type(exc).__name__}: {exc!r} "
            f"— requesting interactive credential for user={uid_tag}"
        )
        await cred_mgr.request_credential(tool_context)
        return None

    if not credential:
        logger.warning(
            f"[iam-flow:{tool_name}] get_auth_credential returned None "
            f"— IAM connector has no stored credential for user={uid_tag}, connector={connector!r}. "
            f"Requesting interactive credential."
        )
        await cred_mgr.request_credential(tool_context)
        return None

    token = extract_and_validate_token(credential, tool_name)

    if not token:
        logger.warning(
            f"[iam-flow:{tool_name}] token absent after IAM connector call "
            f"— requesting interactive credential for user={uid_tag}"
        )
        await cred_mgr.request_credential(tool_context)
        return None

    logger.info(f"[iam-flow:{tool_name}] building {api_name}/{version} service client")
    creds = Credentials(token=token)
    return build(api_name, version, credentials=creds, cache_discovery=False)
