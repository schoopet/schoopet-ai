"""OAuth HTTP handler for Google OAuth flow."""
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import get_settings
from .hmac_token import validate_oauth_init_token
from ..email.handler import register_gmail_watch

logger = logging.getLogger(__name__)

# Module-level references to be initialized via init_oauth_services()
_oauth_manager = None
_session_manager = None

# Google OAuth authorization endpoint
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

router = APIRouter(prefix="/oauth", tags=["oauth"])


def init_oauth_services(oauth_manager, session_manager=None):
    """Initialize OAuth services for the handler.

    Args:
        oauth_manager: OAuthManager instance.
        session_manager: SessionManager instance (optional, for opt-in verification).
    """
    global _oauth_manager, _session_manager
    _oauth_manager = oauth_manager
    _session_manager = session_manager


def _success_html(email: str, service_name: str = "Google Calendar") -> str:
    """Generate success page HTML."""
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
            .success-icon {{
                font-size: 4rem;
                margin-bottom: 1rem;
            }}
            h1 {{
                color: #22c55e;
                margin-bottom: 0.5rem;
            }}
            p {{
                color: #666;
                line-height: 1.6;
            }}
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
            <p>Your {service_name} has been connected.</p>
            <div class="email">{email}</div>
            <p>You can now close this window and use Schoopet.</p>
        </div>
    </body>
    </html>
    """


def _error_html(message: str) -> str:
    """Generate error page HTML."""
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
            .error-icon {{
                font-size: 4rem;
                margin-bottom: 1rem;
            }}
            h1 {{
                color: #ef4444;
                margin-bottom: 0.5rem;
            }}
            p {{
                color: #666;
                line-height: 1.6;
            }}
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
            <div class="message">{message}</div>
            <p>Please try again by requesting a new authorization link from Schoopet.</p>
        </div>
    </body>
    </html>
    """


@router.get("/google/initiate")
async def initiate_oauth(
    request: Request,
    token: str = Query(..., description="HMAC-signed initiation token"),
    feature: str = Query("google", description="Feature to authorize"),
):
    """Initiate Google OAuth flow using HMAC-signed token.

    Args:
        request: FastAPI request (for accessing app state).
        token: HMAC-signed initiation token containing user ID.
        feature: Feature to authorize.

    Returns:
        Redirect to Google OAuth consent page.
    """
    if not _oauth_manager:
        raise HTTPException(status_code=503, detail="OAuth service not initialized")

    # Get HMAC secret from app state
    hmac_secret = getattr(request.app.state, "oauth_hmac_secret", None)
    if not hmac_secret:
        logger.error("HMAC secret not loaded")
        raise HTTPException(status_code=503, detail="OAuth service not properly configured")

    # Validate HMAC token and extract user ID
    user_id = validate_oauth_init_token(token, hmac_secret)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired authorization link. Please request a new one from the assistant.",
        )

    if _session_manager:
        session = await _session_manager.get_session(user_id)
        if not session or not session.opted_in:
            raise HTTPException(
                status_code=403,
                detail="User not registered. Please send a message first.",
            )

    settings = get_settings()

    # Get scopes for feature
    scopes = settings.OAUTH_SCOPES.get(feature)
    if not scopes:
        # Fallback to calendar/default if feature unknown, or raise error
        # For security, better to reject unknown features
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")

    # Generate state for CSRF protection
    state = await _oauth_manager.generate_state(user_id, feature)

    # Build OAuth authorization URL
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "access_type": "offline",  # Request refresh token
        "prompt": "consent",  # Force consent to get refresh token
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    # Log enough to diagnose redirect issues without leaking secrets
    client_id_hint = (settings.GOOGLE_OAUTH_CLIENT_ID[:8] + "****") if settings.GOOGLE_OAUTH_CLIENT_ID else "<EMPTY>"
    logger.info(
        f"Initiating OAuth for user {user_id[:4]}****, feature: {feature}, "
        f"redirect_uri={settings.GOOGLE_OAUTH_REDIRECT_URI!r}, client_id={client_id_hint}"
    )

    return RedirectResponse(url=auth_url)


@router.get("/google/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
):
    """Handle Google OAuth callback.

    Args:
        code: Authorization code from Google.
        state: State parameter for CSRF validation.
        error: Error code if user denied access.
        error_description: Error description.

    Returns:
        HTML page showing success or failure.
    """
    if not _oauth_manager:
        return HTMLResponse(_error_html("OAuth service not initialized"), status_code=503)

    # Handle user denial or errors
    if error:
        logger.warning(f"OAuth error: {error} - {error_description}")
        return HTMLResponse(_error_html(error_description or error), status_code=400)

    # Validate required parameters
    if not code or not state:
        return HTMLResponse(_error_html("Missing authorization code or state"), status_code=400)

    # Validate state (CSRF protection)
    user_id, feature = await _oauth_manager.validate_state(state)
    if not user_id:
        return HTMLResponse(
            _error_html("Invalid or expired authorization. Please request a new link."),
            status_code=400,
        )

    settings = get_settings()
    scopes = settings.OAUTH_SCOPES.get(feature, settings.OAUTH_SCOPES["google"])

    # Exchange code for tokens
    access_token, refresh_token, expires_in, email = await _oauth_manager.exchange_code_for_tokens(
        code, scopes
    )

    if not access_token or not email:
        return HTMLResponse(_error_html("Failed to complete authorization"), status_code=500)

    # Store tokens securely
    success = await _oauth_manager.store_tokens(
        user_id, email, access_token, refresh_token, expires_in, feature
    )

    if not success:
        return HTMLResponse(_error_html("Failed to save authorization"), status_code=500)

    logger.info(f"OAuth completed for user {user_id[:4]}****, feature: {feature}, email: {email}")

    # Set up Gmail push watch for features that include Gmail scope
    if feature == "google":
        preferred_channel = "discord"
        if _session_manager:
            try:
                session = await _session_manager.get_session(user_id)
                if session and session.channel:
                    preferred_channel = session.channel
            except Exception:
                pass
        try:
            await register_gmail_watch(user_id, email, feature, preferred_channel)
        except Exception as e:
            logger.error(f"Failed to register Gmail watch for {email}: {e}")

    service_name_map = {
        "google": "Google",
    }
    service_name = service_name_map.get(feature, "Google")
    return HTMLResponse(_success_html(email, service_name))


@router.get("/status")
async def oauth_status(
    request: Request,
    token: str = Query(..., description="HMAC-signed token containing user ID"),
    feature: str = "google",
):
    """Check OAuth status for a user and feature.

    Args:
        request: FastAPI request (for accessing app state).
        token: HMAC-signed token containing user ID.
        feature: Feature to check.

    Returns:
        JSON with OAuth status.
    """
    if not _oauth_manager:
        raise HTTPException(status_code=503, detail="OAuth service not initialized")

    # Get HMAC secret from app state
    hmac_secret = getattr(request.app.state, "oauth_hmac_secret", None)
    if not hmac_secret:
        raise HTTPException(status_code=503, detail="OAuth service not properly configured")

    # Validate HMAC token and extract user ID
    user_id = validate_oauth_init_token(token, hmac_secret)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    token_info = await _oauth_manager.get_token_info(user_id, feature)

    if not token_info:
        return {
            "connected": False,
            "email": None,
            "feature": feature,
        }

    return {
        "connected": True,
        "email": token_info.email,
        "feature": feature,
        "expires_at": token_info.expires_at.isoformat(),
        "is_expired": token_info.is_expired(),
    }


@router.delete("/revoke")
async def revoke_oauth(
    request: Request,
    token: str = Query(..., description="HMAC-signed token containing user ID"),
    feature: str = "google",
):
    """Revoke OAuth tokens for a user and feature.

    Args:
        request: FastAPI request (for accessing app state).
        token: HMAC-signed token containing user ID.
        feature: Feature to revoke.

    Returns:
        JSON with revocation status.
    """
    if not _oauth_manager:
        raise HTTPException(status_code=503, detail="OAuth service not initialized")

    # Get HMAC secret from app state
    hmac_secret = getattr(request.app.state, "oauth_hmac_secret", None)
    if not hmac_secret:
        raise HTTPException(status_code=503, detail="OAuth service not properly configured")

    # Validate HMAC token and extract user ID
    user_id = validate_oauth_init_token(token, hmac_secret)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    success = await _oauth_manager.revoke_tokens(user_id, feature)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to revoke tokens")

    logger.info(f"OAuth revoked for user {user_id[:4]}****, feature: {feature}")
    return {"revoked": True, "feature": feature}
