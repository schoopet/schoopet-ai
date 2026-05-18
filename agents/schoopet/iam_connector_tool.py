"""Diagnostic tool for querying the IAM connector directly.

Uses the official google-cloud-iamconnectorcredentials library (installed as
a dependency of google-adk[agent-identity]) to call credentials:retrieve,
bypassing ADK's GcpAuthProvider and its session-state cache. Lets the model
inspect what credential state the connector actually holds for a user.
"""
import logging
import os
from datetime import datetime, timezone

from google.cloud import iamconnectorcredentials_v1alpha as icc

from .gcp_auth import GOOGLE_PERSONAL_SCOPES, build_auth_config

logger = logging.getLogger(__name__)


async def check_connector_credential(tool_context=None, target_user_id: str = "") -> dict:
    """Query the IAM connector to inspect what credential it holds for a user.

    Calls credentials:retrieve directly via the official IAM connector client
    library, bypassing ADK's GcpAuthProvider and any session-state caching.
    Use this to diagnose whether a credential is actually stored in the connector.

    Args:
        target_user_id: User ID to query. Defaults to the current session user.

    Returns:
        Dict with has_credential, token_prefix, header, expire_time, expired,
        valid_seconds, scopes, connector, user_id, and error keys.
    """
    connector_name = os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", "")
    if not connector_name:
        return {"error": "IAM_CONNECTOR_GOOGLE_PERSONAL_NAME env var not set"}

    user_id = target_user_id or (getattr(tool_context, "user_id", "") if tool_context else "")
    if not user_id:
        return {"error": "No user_id available — pass target_user_id or call from a user session"}

    uid_tag = (user_id[:4] + "****") if len(user_id) >= 4 else user_id
    continue_uri = build_auth_config(user_id).auth_scheme.continue_uri or ""

    try:
        client = icc.IAMConnectorCredentialsServiceAsyncClient()
        operation = await client.retrieve_credentials(
            connector=connector_name,
            user_id=user_id,
            scopes=GOOGLE_PERSONAL_SCOPES,
            continue_uri=continue_uri,
        )
        response: icc.RetrieveCredentialsResponse = await operation.result()
    except Exception as exc:
        logger.error("[iam-connector-tool] retrieve_credentials failed for user=%s: %s", uid_tag, exc)
        return {"error": str(exc), "user_id": user_id, "connector": connector_name}

    token: str = response.token or ""
    header: str = response.header or ""
    scopes: list = list(response.scopes) if response.scopes else []
    expire_time_pb = response.expire_time

    token_prefix = (token[:8] + "...") if token else "(empty)"

    result: dict = {
        "has_credential": bool(token),
        "token_prefix": token_prefix,
        "header": header,
        "scopes": scopes,
        "user_id": user_id,
        "connector": connector_name,
    }

    if expire_time_pb and expire_time_pb.seconds:
        dt = datetime.fromtimestamp(expire_time_pb.seconds, tz=timezone.utc)
        expire_iso = dt.isoformat()
        result["expire_time"] = expire_iso
        now = datetime.now(timezone.utc)
        if dt <= now:
            result["expired"] = True
            result["expired_seconds_ago"] = round((now - dt).total_seconds())
        else:
            result["expired"] = False
            result["valid_seconds"] = round((dt - now).total_seconds())
    else:
        result["expire_time"] = None

    logger.info(
        "[iam-connector-tool] user=%s has_credential=%s token_prefix=%s expire=%s",
        uid_tag, result["has_credential"], token_prefix, result.get("expire_time"),
    )
    return result
