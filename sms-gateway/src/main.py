"""FastAPI application for Shoopet SMS Gateway."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google.cloud import firestore

from .config import get_settings
from .agent.client import AgentEngineClient
from .session.manager import SessionManager
from .sms.sender import SMSSender
from .sms.splitter import SMSSplitter
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

    # Initialize SMS Sender
    sms_sender = SMSSender(
        account_sid=settings.TWILIO_ACCOUNT_SID,
        auth_token=settings.TWILIO_AUTH_TOKEN,
        from_number=settings.TWILIO_PHONE_NUMBER,
        segment_delay_ms=settings.SMS_SEGMENT_DELAY_MS,
    )

    # Initialize Twilio Validator
    validator = TwilioValidator(settings.TWILIO_AUTH_TOKEN)

    # Initialize services for webhook handler
    init_services(
        validator=validator,
        session_manager=session_manager,
        agent_client=agent_client,
        sms_sender=sms_sender,
    )

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

# Include webhook router
app.include_router(webhook_router)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint with service info."""
    settings = get_settings()
    return {
        "service": "Shoopet SMS Gateway",
        "project": settings.GOOGLE_CLOUD_PROJECT,
        "agent_engine_id": settings.AGENT_ENGINE_ID,
    }
