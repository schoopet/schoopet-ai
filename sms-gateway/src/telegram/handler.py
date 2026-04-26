"""Telegram webhook handler for incoming messages."""
import asyncio
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from ..config import get_settings
from ..messages import RATE_LIMIT_MSG, WELCOME_MSG

logger = logging.getLogger(__name__)

router = APIRouter()

# Global references to services (initialized by main.py)
_validator = None
_session_manager = None
_agent_client = None
_telegram_sender = None
_rate_limiter = None


def init_services(validator, session_manager, agent_client, telegram_sender, rate_limiter=None):
    """Initialize service references for the Telegram handler.

    Called by main.py during application startup.
    """
    global _validator, _session_manager, _agent_client, _telegram_sender, _rate_limiter
    _validator = validator
    _session_manager = session_manager
    _agent_client = agent_client
    _telegram_sender = telegram_sender
    _rate_limiter = rate_limiter


@router.post("/webhook/telegram")
async def handle_telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Handle incoming Telegram webhook update.

    Validates the secret token header, extracts message data, and schedules
    background processing. Returns 200 immediately to acknowledge receipt.
    """
    settings = get_settings()

    # Validate secret token
    if settings.ENABLE_SIGNATURE_VALIDATION:
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not _validator.validate(secret_header):
            logger.warning("Invalid Telegram webhook secret")
            raise HTTPException(status_code=403, detail="Invalid secret token")

    # Parse JSON body
    update = await request.json()

    # Only handle message updates
    message = update.get("message")
    if not message:
        return Response(status_code=200)

    # Extract fields
    chat_id = str(message["chat"]["id"])
    user_id = str(message["from"]["id"])
    text = message.get("text", "").strip()

    if not text:
        return Response(status_code=200)

    logger.info(
        f"Received Telegram message: user={user_id}, chat={chat_id}, "
        f"text length={len(text)}"
    )

    # Schedule background processing
    background_tasks.add_task(
        process_telegram_message,
        user_id=user_id,
        chat_id=chat_id,
        message=text,
    )

    return Response(status_code=200)


async def process_telegram_message(
    user_id: str,
    chat_id: str,
    message: str,
) -> None:
    """Process incoming Telegram message in the background.

    Simplified flow (no opt-in required for Telegram):
    1. /start -> auto opt-in + welcome message
    2. All other messages -> forward to agent (auto opt-in on first message)
    """
    start_time = time.time()

    try:
        # Get or create user record
        session_info = await _session_manager.get_or_create_user(user_id)

        # Handle /start command
        if message == "/start":
            if not session_info.opted_in:
                await _session_manager.set_opted_in(user_id, channel="telegram")
            await _telegram_sender.send(chat_id, WELCOME_MSG)
            await _session_manager.update_last_activity(user_id, channel="telegram")
            return

        # Auto opt-in on first message (no explicit consent needed for Telegram)
        if session_info.is_new_user or not session_info.opted_in:
            logger.info(f"Auto opt-in for Telegram user {user_id}")
            await _session_manager.set_opted_in(user_id, channel="telegram")

        # Check rate limit
        if _rate_limiter:
            is_allowed, count = await _rate_limiter.check_and_increment(user_id)
            if not is_allowed:
                logger.warning(f"Rate limit exceeded for Telegram user {user_id}: {count} messages today")
                await _telegram_sender.send(chat_id, RATE_LIMIT_MSG)
                return

        # Get or create agent session
        session_info = await _session_manager.get_or_create_session(user_id, channel="telegram")

        logger.info(
            f"Forwarding to agent for Telegram user {user_id}: "
            f"session={session_info.agent_session_id}, "
            f"is_new_session={session_info.is_new_session}"
        )

        # Query the agent
        try:
            response = await _agent_client.send_message(
                user_id=user_id,
                session_id=session_info.agent_session_id,
                message=message,
            )
        except asyncio.TimeoutError:
            logger.error(f"Agent timeout for Telegram user {user_id}")
            await _telegram_sender.send(
                chat_id,
                "I'm taking longer than usual to respond. Please try again in a moment.",
            )
            return

        if not response:
            logger.warning(f"Empty response from agent for Telegram user {user_id}")
            await _telegram_sender.send(
                chat_id,
                "I couldn't generate a response. Please try again.",
            )
            return

        # Send response
        await _telegram_sender.send(chat_id, response)

        # Update session activity
        await _session_manager.update_last_activity(user_id, channel="telegram")

        processing_time = (time.time() - start_time) * 1000
        logger.info(
            f"Processed Telegram message in {processing_time:.0f}ms: "
            f"response sent to {user_id} ({len(response)} chars)"
        )

    except Exception as e:
        logger.exception(f"Error processing Telegram message for {user_id}: {e}")
        try:
            await _telegram_sender.send(
                chat_id,
                "Something went wrong. Please try again.",
            )
        except Exception as send_err:
            logger.error(f"Failed to send error message to Telegram {chat_id}: {send_err}")
