"""FastAPI application for Shoopet SMS Gateway."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google.cloud import firestore
from google.cloud import secretmanager

from .config import get_settings
from .agent.client import AgentEngineClient
from .internal.handler import router as internal_router, init_internal_services
from .internal.auth import init_allowed_service_accounts, set_internal_hmac_secret
from .oauth.handler import router as oauth_router, init_oauth_services
from .oauth.manager import OAuthManager
from .oauth.secret_manager import SecretManagerClient
from .ratelimit.limiter import RateLimiter
from .session.manager import SessionManager
from .sms.sender import SMSSender
from .webhook.handler import router as webhook_router, init_services
from .webhook.validator import TwilioValidator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    settings = get_settings()

    logger.info("Starting SMS Gateway...")
    logger.info(f"Project: {settings.GOOGLE_CLOUD_PROJECT}")
    logger.info(f"Location: {settings.GOOGLE_CLOUD_LOCATION}")
    logger.info(f"Agent Engine: {settings.AGENT_ENGINE_ID}")

    # Initialize Firestore client
    firestore_client = firestore.AsyncClient(
        project=settings.GOOGLE_CLOUD_PROJECT,
    )

    # Initialize Agent Engine client
    agent_client = AgentEngineClient(
        project_id=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
        agent_engine_id=settings.AGENT_ENGINE_ID,
        timeout_seconds=settings.AGENT_TIMEOUT_SECONDS,
    )

    # Initialize Session Manager
    session_manager = SessionManager(
        firestore_client=firestore_client,
        agent_client=agent_client,
        timeout_minutes=settings.SESSION_TIMEOUT_MINUTES,
    )

    # Initialize SMS/WhatsApp Sender
    sms_sender = SMSSender(
        account_sid=settings.TWILIO_ACCOUNT_SID,
        auth_token=settings.TWILIO_AUTH_TOKEN,
        from_number=settings.TWILIO_PHONE_NUMBER,
        whatsapp_from_number=settings.TWILIO_WHATSAPP_NUMBER or settings.TWILIO_PHONE_NUMBER,
    )

    # Initialize Twilio Validator
    validator = TwilioValidator(settings.TWILIO_AUTH_TOKEN)

    # Initialize Rate Limiter
    rate_limiter = RateLimiter(
        firestore_client=firestore_client,
        daily_limit=settings.DAILY_MESSAGE_LIMIT,
        excluded_phones=settings.RATE_LIMIT_EXCLUDED_PHONES,
    )
    logger.info(f"Rate limiting: {settings.DAILY_MESSAGE_LIMIT}/day, {len(settings.RATE_LIMIT_EXCLUDED_PHONES)} excluded phones")

    # Initialize services for webhook handler
    init_services(
        validator=validator,
        session_manager=session_manager,
        agent_client=agent_client,
        sms_sender=sms_sender,
        rate_limiter=rate_limiter,
    )

    # Initialize internal services for async task handling
    init_internal_services(
        session_manager=session_manager,
        agent_client=agent_client,
        sms_sender=sms_sender,
    )
    init_allowed_service_accounts()
    logger.info("Internal services initialized")

    # Initialize OAuth services (if configured)
    oauth_manager = None
    if settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET:
        logger.info("Initializing OAuth services...")
        secret_manager_client = SecretManagerClient(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
        )
        oauth_manager = OAuthManager(
            firestore_client=firestore_client,
            secret_manager=secret_manager_client,
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
            state_ttl_seconds=settings.OAUTH_STATE_TTL_SECONDS,
        )
        init_oauth_services(
            oauth_manager=oauth_manager,
            session_manager=session_manager,
        )

        # Load HMAC secret for OAuth token validation
        try:
            sm_client = secretmanager.SecretManagerServiceClient()
            secret_name = f"projects/{settings.GOOGLE_CLOUD_PROJECT}/secrets/oauth-hmac-secret/versions/latest"
            response = sm_client.access_secret_version(request={"name": secret_name})
            app.state.oauth_hmac_secret = response.payload.data.decode("UTF-8")
            logger.info("OAuth HMAC secret loaded from Secret Manager")
        except Exception as e:
            logger.error(f"Failed to load OAuth HMAC secret: {e}")
            app.state.oauth_hmac_secret = None

        logger.info("OAuth services initialized")
    else:
        logger.warning("OAuth not configured - calendar features disabled")
        app.state.oauth_hmac_secret = None

    # Load internal HMAC secret for service-to-service auth
    try:
        sm_client = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{settings.GOOGLE_CLOUD_PROJECT}/secrets/internal-hmac-secret/versions/latest"
        response = sm_client.access_secret_version(request={"name": secret_name})
        internal_secret = response.payload.data.decode("UTF-8")
        set_internal_hmac_secret(internal_secret)
        logger.info("Internal HMAC secret loaded from Secret Manager")
    except Exception as e:
        logger.warning(f"Internal HMAC secret not available: {e} (OIDC auth will still work)")

    logger.info("SMS Gateway started successfully")

    yield

    # Cleanup
    logger.info("Shutting down SMS Gateway...")


# Create FastAPI application
app = FastAPI(
    title="Shoopet SMS Gateway",
    description="Twilio SMS integration for Vertex AI Agent Engine",
    version="1.0.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(webhook_router)
app.include_router(oauth_router)
app.include_router(internal_router)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Shoopet SMS Gateway",
        "status": "running",
    }
