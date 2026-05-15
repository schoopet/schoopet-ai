"""OAuth HTTP handler — IAM connector callback only."""
import html
import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth.connector import finalize_iam_credentials
from ..email.handler import register_gmail_watch
from ..email.gmail_client import get_gmail_token, get_user_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])

_session_manager = None
_agent_client = None
_discord_sender = None


def init_oauth_services(session_manager=None, agent_client=None, discord_sender=None):
    """Initialize OAuth services for the handler."""
    global _session_manager, _agent_client, _discord_sender
    _session_manager = session_manager
    _agent_client = agent_client
    _discord_sender = discord_sender


def _success_html(email: str, service_name: str = "Google Calendar") -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Authorization Successful</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }}
            .container {{
                background: white;
                padding: 2rem;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 400px;
                margin: 1rem;
            }}
            .success-icon {{ font-size: 4rem; margin-bottom: 1rem; }}
            h1 {{ color: #22c55e; margin-bottom: 0.5rem; }}
            p {{ color: #666; line-height: 1.6; }}
            .email {{
                background: #f0f9ff;
                padding: 0.5rem 1rem;
                border-radius: 6px;
                font-family: monospace;
                color: #0369a1;
                margin: 1rem 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">&#10004;</div>
            <h1>Authorization Successful!</h1>
            <p>Your {html.escape(service_name)} has been connected.</p>
            <div class="email">{html.escape(email)}</div>
            <p>You can now close this window and use Schoopet.</p>
        </div>
    </body>
    </html>
    """


def _error_html(message: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Authorization Failed</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            }}
            .container {{
                background: white;
                padding: 2rem;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 400px;
                margin: 1rem;
            }}
            .error-icon {{ font-size: 4rem; margin-bottom: 1rem; }}
            h1 {{ color: #ef4444; margin-bottom: 0.5rem; }}
            p {{ color: #666; line-height: 1.6; }}
            .message {{
                background: #fef2f2;
                padding: 0.5rem 1rem;
                border-radius: 6px;
                color: #991b1b;
                margin: 1rem 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="error-icon">&#10006;</div>
            <h1>Authorization Failed</h1>
            <div class="message">{html.escape(message)}</div>
            <p>Please try again by requesting a new authorization link from Schoopet.</p>
        </div>
    </body>
    </html>
    """


@router.get("/authorize")
async def oauth_authorize(nonce: str = Query(..., description="Pending credential nonce")):
    """Redirect to the Google OAuth consent page for a pending credential request."""
    if not _session_manager:
        return HTMLResponse(_error_html("Service not initialized."), status_code=503)

    pending = await _session_manager.get_pending_credential(nonce)
    if not pending:
        return HTMLResponse(
            _error_html("Authorization link not found or already used."),
            status_code=404,
        )

    auth_uri = pending.get("auth_uri", "")
    if not auth_uri:
        return HTMLResponse(_error_html("Authorization URI missing."), status_code=404)

    return RedirectResponse(url=auth_uri)


@router.get("/connector/callback", response_class=HTMLResponse)
async def connector_callback(
    state: str = Query(None, description="Nonce from IAM connector consent flow"),
    nonce: str = Query(None, description="Alternate nonce parameter"),
    user_id_validation_state: str = Query(None, description="Opaque state from IAM connector (no nonce echoed)"),
    connector_name: str = Query(None, description="Connector resource name from IAM connector"),
    error: str = Query(None),
    error_description: str = Query(None),
):
    """Finalize IAM connector consent and resume the agent session."""
    if error:
        logger.warning(f"IAM connector callback error: {error} - {error_description}")
        return HTMLResponse(_error_html(error_description or error), status_code=400)

    if not _session_manager:
        logger.error("IAM connector callback: session manager not initialized")
        return HTMLResponse(_error_html("Service not initialized."), status_code=503)

    if not _agent_client:
        logger.error("IAM connector callback: agent client not initialized")
        return HTMLResponse(_error_html("Service not initialized."), status_code=503)

    consent_nonce = state or nonce
    needs_finalize = False

    if consent_nonce:
        pending = await _session_manager.get_pending_credential(consent_nonce)
        if not pending:
            logger.warning(f"IAM connector callback: no pending credential for nonce {consent_nonce[:8]}...")
            return HTMLResponse(
                _error_html("Authorization session not found or already completed."),
                status_code=400,
            )
    elif user_id_validation_state:
        # IAM connector does not echo the nonce — look up the most recent pending credential.
        logger.info(
            f"IAM connector callback via user_id_validation_state "
            f"(connector={connector_name or 'unknown'})"
        )
        consent_nonce, pending = await _session_manager.get_latest_pending_credential()
        if not pending:
            logger.warning("IAM connector callback: no pending credential found")
            return HTMLResponse(
                _error_html("No pending authorization found. Please try again."),
                status_code=400,
            )
        needs_finalize = True
    else:
        logger.warning("IAM connector callback: no state, nonce, or user_id_validation_state")
        return HTMLResponse(_error_html("Missing authorization state."), status_code=400)

    user_id = pending.get("user_id", "")
    session_id = pending.get("session_id", "")
    credential_fc_id = pending.get("credential_function_call_id", "")
    auth_config_dict = pending.get("auth_config_dict") or {}

    logger.info(
        f"IAM connector consent complete for user {user_id[:4]}****, "
        f"session={session_id}, nonce={consent_nonce[:8] if consent_nonce else 'n/a'}..."
    )

    # Call credentials:finalize to store the token in the IAM connector backend.
    # Required per the IAM connector 3LO spec — without this the credential is
    # not stored and subsequent get_auth_credential calls keep returning None.
    if needs_finalize:
        effective_connector = connector_name or pending.get("auth_config_dict", {}).get(
            "authScheme", {}
        ).get("name", "")
        try:
            await finalize_iam_credentials(
                connector_name=effective_connector,
                user_id=user_id,
                consent_nonce=consent_nonce,
                user_id_validation_state=user_id_validation_state,
            )
        except Exception as e:
            logger.error(f"credentials:finalize failed for {user_id[:4]}****: {e}")
            return HTMLResponse(_error_html("Failed to finalize authorization. Please try again."), status_code=500)

    try:
        events = await _agent_client.send_credential_response(
            user_id=user_id,
            session_id=session_id,
            credential_function_call_id=credential_fc_id,
            auth_config_dict=auth_config_dict,
        )
    except Exception as e:
        logger.error(f"IAM connector callback: failed to resume agent for {user_id[:4]}****: {e}")
        return HTMLResponse(_error_html("Failed to resume your session. Please try again."), status_code=500)

    await _session_manager.clear_pending_credential(consent_nonce)

    # Forward the agent's post-consent response to the user via Discord DM.
    if _discord_sender and events:
        from ..agent.client import AgentEngineClient
        response_text = AgentEngineClient.extract_text(events)
        if response_text:
            try:
                await _discord_sender.send(user_id, response_text)
                logger.info(f"Forwarded post-consent agent response to Discord user {user_id[:4]}****")
            except Exception as e:
                logger.warning(f"Failed to forward post-consent response to Discord for {user_id[:4]}****: {e}")

    # Register Gmail watch now that a token is available
    gmail_address = ""
    try:
        token = await get_gmail_token(user_id)
        if token:
            profile = await get_user_profile(token)
            if profile and profile.get("emailAddress"):
                gmail_address = profile["emailAddress"]
                await register_gmail_watch(user_id, gmail_address)
    except Exception as e:
        logger.warning(f"Gmail watch setup failed for {user_id[:4]}****: {e}")

    return HTMLResponse(_success_html(gmail_address or "your Google account", "Google Workspace"))
