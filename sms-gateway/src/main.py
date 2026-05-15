"""FastAPI application for Schoopet SMS Gateway."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google.cloud import firestore

from .config import get_settings
from .agent.client import AgentEngineClient
from .internal.handler import router as internal_router, init_internal_services
from .internal.auth import init_allowed_service_accounts
from .oauth.handler import router as oauth_router, init_oauth_services
from .ratelimit.limiter import RateLimiter
from .session.manager import SessionManager
from .email.handler import router as email_router, init_email_services
from .discord.handler import router as discord_router, init_services as init_discord_services
from .discord.gateway import start_gateway
from .discord.sender import DiscordSender
from .discord.validator import DiscordValidator
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
    logger.info(f"Agent Engine: {settings.PERSONAL_AGENT_ENGINE_ID or '(not configured)'}")

    # Initialize Firestore client
    firestore_client = firestore.AsyncClient(
        project=settings.GOOGLE_CLOUD_PROJECT,
    )

    # Initialize Agent Engine client
    agent_client = (
        AgentEngineClient(
            project_id=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            agent_engine_id=settings.PERSONAL_AGENT_ENGINE_ID,
            timeout_seconds=settings.AGENT_TIMEOUT_SECONDS,
        )
        if settings.PERSONAL_AGENT_ENGINE_ID
        else None
    )

    # Initialize Session Manager
    session_manager = SessionManager(
        firestore_client=firestore_client,
        agent_client=agent_client,
        timeout_minutes=settings.SESSION_TIMEOUT_MINUTES,
    )

    # Initialize Rate Limiter
    rate_limiter = RateLimiter(
        firestore_client=firestore_client,
        daily_limit=settings.DAILY_MESSAGE_LIMIT,
        excluded_phones=settings.RATE_LIMIT_EXCLUDED_PHONES,
    )
    logger.info(f"Rate limiting: {settings.DAILY_MESSAGE_LIMIT}/day, {len(settings.RATE_LIMIT_EXCLUDED_PHONES)} excluded phones")

    # Initialize Discord services (if configured)
    discord_sender = None
    if settings.DISCORD_BOT_TOKEN and settings.DISCORD_PUBLIC_KEY and settings.DISCORD_APPLICATION_ID:
        logger.info("Initializing Discord services...")
        discord_sender = DiscordSender(
            application_id=settings.DISCORD_APPLICATION_ID,
            bot_token=settings.DISCORD_BOT_TOKEN,
        )
        discord_validator = DiscordValidator(public_key=settings.DISCORD_PUBLIC_KEY)
        init_discord_services(
            validator=discord_validator,
            session_manager=session_manager,
            agent_client=agent_client,
            discord_sender=discord_sender,
            rate_limiter=rate_limiter,
        )
        logger.info("Discord services initialized")

        # Start the Gateway for DM and @mention support
        discord_gateway = await start_gateway(
            bot_token=settings.DISCORD_BOT_TOKEN,
            session_manager=session_manager,
            agent_client=agent_client,
            rate_limiter=rate_limiter,
        )
    else:
        discord_gateway = None
        logger.info("Discord not configured (DISCORD_BOT_TOKEN, DISCORD_PUBLIC_KEY, or DISCORD_APPLICATION_ID not set)")

    # Initialize internal services
    init_internal_services(
        agent_client=agent_client,
        firestore_client=firestore_client,
        discord_sender=discord_sender,
    )
    init_allowed_service_accounts()
    logger.info("Internal services initialized")

    # Initialize OAuth connector callback + Email services
    init_oauth_services(
        session_manager=session_manager,
        agent_client=agent_client,
    )
    logger.info("OAuth connector callback initialized")

    init_email_services(
        db=firestore_client,
        agent_client=agent_client,
        session_manager=session_manager,
        discord_sender=discord_sender,
    )
    logger.info("Email services initialized")

    logger.info("SMS Gateway started successfully")

    yield

    # Cleanup
    logger.info("Shutting down SMS Gateway...")
    if discord_gateway:
        await discord_gateway.close()
    if discord_sender:
        await discord_sender.close()


# Create FastAPI application
app = FastAPI(
    title="Schoopet Gateway",
    description="Discord and email gateway for Vertex AI Agent Engine",
    version="1.0.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(discord_router)
app.include_router(email_router)
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
        "service": "Schoopet SMS Gateway",
        "status": "running",
    }
