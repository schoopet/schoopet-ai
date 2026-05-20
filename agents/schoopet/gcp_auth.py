"""GCP Agent Identity auth provider — registers GcpAuthProvider at import time."""
import asyncio
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

# google-auth[pyopenssl] (a transitive dep of google-adk[agent-identity]) injects
# pyopenssl into urllib3 at import time via google.auth.transport.urllib3.
# pyopenssl's SSL Context becomes immutable after creating a Connection.
# GcpAuthProvider._get_client() caches its IAMConnectorCredentialsServiceClient,
# so the cached REST session reuses the same pyopenssl Context.  When urllib3's
# connection-pool TTL expires (~8 min) it tries to mutate the immutable Context:
#   ValueError: Context has already been used to create a Connection,
#              it cannot be mutated again
#
# Fix: monkey-patch _get_client to return a NEW client on every call.  Each call
# gets a fresh HTTP session with a fresh pyopenssl Context, so the immutability
# invariant is never violated.
#
# We intentionally leave pyopenssl injected into urllib3 globally because the
# Agent Engine's mTLS channel (configure_mtls_channel / _MutualTlsAdapter) accesses
# ctx_poolmanager._ctx which only exists on pyopenssl pool managers; removing
# pyopenssl globally breaks GCS artifact loading and telemetry on every request.
def _fresh_iam_client(self):
    from google.cloud.iamconnectorcredentials_v1alpha import IAMConnectorCredentialsServiceClient
    from google.api_core.client_options import ClientOptions
    client_options = None
    if host := os.environ.get("IAM_CONNECTOR_CREDENTIALS_TARGET_HOST"):
        client_options = ClientOptions(api_endpoint=host)
    return IAMConnectorCredentialsServiceClient(client_options=client_options, transport="rest")

GcpAuthProvider._get_client = _fresh_iam_client


# ADK hardcodes force_refresh=False in GcpAuthProvider._retrieve_credentials, which
# means the connector never silently refreshes an expired access token — it returns
# uri_consent_required instead.  With force_refresh=True the connector will use its
# stored refresh token to silently obtain a new access token without user interaction.
async def _force_refresh_retrieve(self, user_id: str, auth_scheme) -> object:
    from google.cloud.iamconnectorcredentials_v1alpha import RetrieveCredentialsRequest
    import asyncio as _asyncio
    # force_refresh was removed from the v1alpha API; the connector now refreshes implicitly.
    request = RetrieveCredentialsRequest(
        connector=auth_scheme.name,
        user_id=user_id,
        scopes=auth_scheme.scopes,
        continue_uri=auth_scheme.continue_uri or "",
    )
    operation = await _asyncio.to_thread(self._get_client().retrieve_credentials, request)
    return operation.operation

GcpAuthProvider._retrieve_credentials = _force_refresh_retrieve

# pyopenssl marks a Context as immutable after the first Connection is created
# from it (OpenSSL.SSL.Context._used = True, enforced by @_require_not_used on
# every mutating method).  urllib3 tries to set verify_mode on the same Context
# when reconnecting after the pool TTL (~8 min) → ValueError crash.
#
# This affects ANY long-lived urllib3 client in the process that ends up with a
# pyopenssl context — including the OpenTelemetry Cloud Trace exporter, which
# has its own cached HTTP session and cannot be patched at the call site.
#
# Fix: override _used as a class-level property that always returns False and
# silently ignores writes.  This disables the immutability guard globally.
# Allowing reconfiguration before a new connection handshake is safe; the guard
# exists to prevent mid-handshake mutation, not pre-connection reconfiguration.
try:
    import OpenSSL.SSL
    OpenSSL.SSL.Context._used = property(lambda self: False, lambda self, v: None)
    logger.info("[ssl-patch] OpenSSL.SSL.Context._used immutability guard disabled")
except Exception as _e:
    logger.warning("[ssl-patch] failed to patch OpenSSL.SSL.Context._used: %r", _e)

_iam_lock = asyncio.Lock()

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
    logger.info(f"{tag} credential received: auth_type={auth_type}, has_http={has_http}")

    try:
        http_creds = credential.http.credentials
        token: str | None = http_creds.token
    except AttributeError as e:
        logger.warning(f"{tag} unexpected credential structure: {e!r} — credential={credential!r}", exc_info=True)
        return None

    if not token:
        logger.warning(
            f"{tag} token is empty — IAM connector returned a credential with no access token; "
            f"the stored credential may be corrupted or not yet fully propagated. "
            f"NOTE: force_refresh field was removed from the v1alpha API; "
            f"the IAM connector returned an empty credential."
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

    Calls GcpAuthProvider → IAM connector RetrieveCredentials
    on every invocation. Logs every step so the full credential flow is visible in
    Cloud Logging for debugging token lifecycle issues.

    Returns a googleapiclient service, or None after emitting a credential request.
    Only triggers the OAuth consent flow when the IAM connector explicitly returns
    uri_consent_required (credential.auth_type == OAUTH2). Transport errors and
    missing tokens do NOT trigger consent — they return None silently so the agent
    can surface a clean error rather than spamming users with consent prompts.
    """
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.adk.auth.auth_credential import AuthCredentialTypes

    connector = os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", "<IAM_CONNECTOR_GOOGLE_PERSONAL_NAME unset>")
    continue_uri = os.getenv("IAM_CONNECTOR_CONTINUE_URI", "<IAM_CONNECTOR_CONTINUE_URI unset>")
    user_id: str = getattr(tool_context, "user_id", "") or ""
    logger.info(
        f"[iam-flow:{tool_name}] get_auth_credential → IAM connector "
        f"connector={connector!r} user={user_id} api={api_name}/{version} "
        f"scopes={GOOGLE_PERSONAL_SCOPES} continue_uri={continue_uri!r}"
    )

    cred_mgr = CredentialManager(auth_config=build_auth_config(user_id))
    async with _iam_lock:
        try:
            credential = await cred_mgr.get_auth_credential(tool_context)
        except Exception as exc:
            # Transport/service error — NOT a consent issue. Log and return None
            # without triggering a consent prompt so users aren't spammed when the
            # IAM connector or network is temporarily unavailable.
            cause = exc.__cause__ or exc.__context__
            cause_detail = f" caused_by={type(cause).__name__}: {cause!r}" if cause else ""
            http_status = None
            for ex in (exc, cause):
                if ex is None:
                    continue
                http_status = (
                    getattr(getattr(ex, "resp", None), "status", None)
                    or getattr(ex, "status_code", None)
                    or getattr(ex, "code", None)
                )
                if http_status:
                    break
            logger.warning(
                f"[iam-flow:{tool_name}] get_auth_credential raised {type(exc).__name__}: {exc!r}"
                f"{cause_detail} http_status={http_status}"
                f" — transport/service error, NOT triggering consent for user={user_id}",
                exc_info=True,
            )
            return None

    if not credential:
        # For CustomAuthScheme (GcpAuthProviderScheme), CredentialManager returns None
        # specifically when the IAM connector signalled uri_consent_required: it stores
        # the auth_uri in exchanged_auth_credential and returns None to tell us to call
        # request_credential.  This is distinct from an exception (transport error) which
        # we catch above and do NOT consent-prompt for.
        logger.info(
            f"[iam-flow:{tool_name}] get_auth_credential returned None "
            f"— IAM connector returned uri_consent_required for user={user_id}; "
            f"requesting interactive credential"
        )
        await cred_mgr.request_credential(tool_context)
        return None

    # Defensive check: auth_type==OAUTH2 cannot occur here for CustomAuthScheme
    # (CredentialManager handles it internally and returns None above), but guard
    # against future ADK changes returning it directly.
    if credential.auth_type == AuthCredentialTypes.OAUTH2:
        auth_uri = getattr(getattr(credential, "oauth2", None), "auth_uri", None)
        if not auth_uri:
            logger.warning(
                f"[iam-flow:{tool_name}] credential.auth_type=OAUTH2 but no auth_uri "
                f"for user={user_id} — NOT triggering consent (malformed connector response)"
            )
            return None
        logger.info(
            f"[iam-flow:{tool_name}] credential.auth_type=OAUTH2 with auth_uri "
            f"for user={user_id} — requesting interactive credential"
        )
        await cred_mgr.request_credential(tool_context)
        return None

    token = extract_and_validate_token(credential, tool_name)

    if not token:
        logger.warning(
            f"[iam-flow:{tool_name}] token absent after IAM connector call "
            f"for user={user_id} — NOT triggering consent (token may be temporarily unavailable)"
        )
        return None

    logger.info(f"[iam-flow:{tool_name}] building {api_name}/{version} service client")
    creds = Credentials(token=token)
    return build(api_name, version, credentials=creds, cache_discovery=False)
