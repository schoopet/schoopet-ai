"""Slack webhook handler for incoming messages."""
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
_slack_sender = None
_rate_limiter = None


def init_slack_services(validator, session_manager, agent_client, slack_sender, rate_limiter=None):
    """Initialize service references for the Slack handler.

    Called by main.py during application startup.
    """
    global _validator, _session_manager, _agent_client, _slack_sender, _rate_limiter
    _validator = validator
    _session_manager = session_manager
    _agent_client = agent_client
    _slack_sender = slack_sender
    _rate_limiter = rate_limiter


@router.post("/webhook/slack")
async def handle_slack_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Handle incoming Slack Events API request.

    Handles:
    - url_verification challenge (synchronously echoed back)
    - message.im events (DMs to the bot)

    Returns 200 immediately to acknowledge receipt, with background processing
    for actual messages.
    """
    settings = get_settings()

    # Suppress Slack retries by returning 200 immediately
    if request.headers.get("X-Slack-Retry-Num"):
        logger.info("Suppressing Slack retry request")
        return Response(status_code=200)

    body_bytes = await request.body()

    # Validate Slack signing secret
    if settings.ENABLE_SIGNATURE_VALIDATION:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if not _validator.validate(timestamp, body_bytes, signature):
            logger.warning("Invalid Slack request signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    event_type = payload.get("type")

    # Handle Slack's url_verification challenge — must respond synchronously
    if event_type == "url_verification":
        challenge = payload.get("challenge", "")
        logger.info("Responding to Slack URL verification challenge")
        return Response(content=challenge, media_type="text/plain")

    # Only process event_callback payloads
    if event_type != "event_callback":
        return Response(status_code=200)

    event = payload.get("event", {})

    # Ignore bot messages to prevent infinite loops
    if event.get("bot_id"):
        return Response(status_code=200)

    # Only handle DM messages (message.im)
    if event.get("type") != "message" or event.get("channel_type") != "im":
        return Response(status_code=200)

    user_id = event.get("user")
    text = event.get("text", "").strip()

    if not user_id or not text:
        return Response(status_code=200)

    event_id = payload.get("event_id", "")
    team_id = payload.get("team_id", "")
    logger.info(
        f"Received Slack DM: user={user_id}, team={team_id}, event_id={event_id}, "
        f"text length={len(text)}"
    )

    background_tasks.add_task(
        process_slack_message,
        user_id=user_id,
        message=text,
        team_id=team_id,
    )

    return Response(status_code=200)


async def process_slack_message(user_id: str, message: str, team_id: str = "") -> None:
    """Process incoming Slack DM in the background.

    Flow (no opt-in required for Slack workspace users):
    1. Get or create user record
    2. Auto opt-in on first message
    3. Send immediate ack
    4. Rate limit check
    5. Forward to agent and update ack with response
    """
    start_time = time.time()
    channel_id: str | None = None
    ack_ts: str | None = None

    async def _reply(text: str) -> None:
        """Update ack message or fall back to new message."""
        if channel_id and ack_ts:
            await _slack_sender.update_message(channel_id, ack_ts, text)
        else:
            await _slack_sender.send(user_id, text)

    try:
        # Get or create user record
        session_info = await _session_manager.get_or_create_user(user_id)

        # Auto opt-in on first message (workspace-authorized users)
        if session_info.is_new_user or not session_info.opted_in:
            logger.info(f"Auto opt-in for Slack user {user_id}")
            await _session_manager.set_opted_in(user_id, agent_type="team")

        # Acknowledge immediately before any slow operations
        t_before_ack = time.time()
        channel_id, ack_ts = await _slack_sender.send_ack(user_id)
        ack_ms = (time.time() - t_before_ack) * 1000

        # Check rate limit
        if _rate_limiter:
            is_allowed, count = await _rate_limiter.check_and_increment(user_id)
            if not is_allowed:
                logger.warning(
                    f"Rate limit exceeded for Slack user {user_id}: {count} messages today"
                )
                await _reply(RATE_LIMIT_MSG)
                return

        # Get or create agent session
        session_info = await _session_manager.get_or_create_session(user_id, agent_type="team")

        logger.info(
            f"Forwarding to agent for Slack user {user_id}: "
            f"session={session_info.agent_session_id}, "
            f"is_new_session={session_info.is_new_session}"
        )

        # Store slack_team_id before agent runs so tools can read it from Firestore
        await _session_manager.update_last_activity(user_id, channel="slack", slack_team_id=team_id)

        # Query the agent
        t_before_agent = time.time()
        try:
            response = await _agent_client.send_message(
                user_id=user_id,
                session_id=session_info.agent_session_id,
                message=message,
            )
        except asyncio.TimeoutError:
            logger.error(f"Agent timeout for Slack user {user_id}")
            await _reply("I'm taking longer than usual to respond. Please try again in a moment.")
            return
        agent_ms = (time.time() - t_before_agent) * 1000

        if not response:
            logger.warning(f"Empty response from agent for Slack user {user_id}")
            await _reply("I couldn't generate a response. Please try again.")
            return

        # Replace ack with actual response (overflow chunks posted as new messages)
        t_before_send = time.time()
        await _slack_sender.send_response(channel_id, ack_ts, response)
        send_ms = (time.time() - t_before_send) * 1000

        total_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Processed Slack message in {total_ms:.0f}ms "
            f"[ack={ack_ms:.0f}ms, agent={agent_ms:.0f}ms, send={send_ms:.0f}ms]: "
            f"response sent to {user_id} ({len(response)} chars)"
        )

    except Exception as e:
        logger.exception(f"Error processing Slack message for {user_id}: {e}")
        try:
            await _reply("Something went wrong. Please try again.")
        except Exception as send_err:
            logger.error(f"Failed to send error message to Slack user {user_id}: {send_err}")
