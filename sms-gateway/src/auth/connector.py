"""IAM Connector gateway helpers."""
import asyncio
import logging

logger = logging.getLogger(__name__)

_FINALIZE_BASE = "https://iamconnectorcredentials.googleapis.com/v1alpha"


async def finalize_iam_credentials(
    connector_name: str,
    user_id: str,
    consent_nonce: str,
    user_id_validation_state: str,
) -> None:
    """Call credentials:finalize on the IAM connector REST API.

    Required after OAuth consent when the callback carries user_id_validation_state
    instead of echoing the nonce. Without this call the token is never stored in
    the IAM connector backend and subsequent get_auth_credential calls return None.
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
        f"[connector] credentials:finalize → POST {finalize_url} "
        f"user_id={user_id!r} "
        f"nonce={consent_nonce[:8] if consent_nonce else 'n/a'}... "
        f"user_id_validation_state={user_id_validation_state[:16] if user_id_validation_state else 'n/a'}..."
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            finalize_url,
            json=payload,
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        if resp.is_success:
            logger.info(
                f"[connector] credentials:finalize succeeded: "
                f"status={resp.status_code} user_id={user_id!r} "
                f"body={resp.text[:200] if resp.text else '(empty)'}"
            )
        else:
            logger.error(
                f"[connector] credentials:finalize failed: "
                f"status={resp.status_code} user_id={user_id!r} "
                f"nonce={consent_nonce[:8] if consent_nonce else 'n/a'}... "
                f"body={resp.text[:400]}"
            )
        resp.raise_for_status()
