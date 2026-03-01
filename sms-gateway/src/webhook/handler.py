"""Twilio webhook handler for incoming SMS and WhatsApp messages."""
import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from ..channel import MessageChannel
from ..config import get_settings
from ..messages import RATE_LIMIT_MSG_SMS as RATE_LIMIT_MSG

logger = logging.getLogger(__name__)

router = APIRouter()

# Empty TwiML response to acknowledge webhook immediately
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Opt-in compliance messages
OPT_IN_REQUEST_MSG = (
    "By replying YES, you agree to receive one-to-one SMS messages in response "
    "to your requests. Msg frequency varies. Reply STOP to cancel or HELP for help."
)
OPT_IN_SUCCESS_MSG = (
    "You're all set! Send me a message and I'll help you out. "
    "Reply STOP anytime to unsubscribe."
)
OPT_OUT_MSG = (
    "You've been unsubscribed and will no longer receive messages. "
    "Reply YES anytime to opt back in."
)
HELP_MSG = (
    "Schoopet SMS Assistant: Send a message to get started. "
    "Reply STOP to unsubscribe. Msg frequency varies. "
    "For more help, visit our website."
)
NOT_OPTED_IN_MSG = (
    "Please reply YES to opt in and start using this service. "
    "Reply HELP for more info."
)

# Global references to services (initialized by main.py)
_validator = None
_session_manager = None
_agent_client = None
_sms_sender = None
_rate_limiter = None


def init_services(validator, session_manager, agent_client, sms_sender, rate_limiter=None):
    """Initialize service references for the webhook handler.

    Called by main.py during application startup.
    """
    global _validator, _session_manager, _agent_client, _sms_sender, _rate_limiter
    _validator = validator
    _session_manager = session_manager
    _agent_client = agent_client
    _sms_sender = sms_sender
    _rate_limiter = rate_limiter


@router.post("/webhook/sms")
async def handle_incoming_sms(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Handle incoming SMS webhook from Twilio.

    Validates the request signature, extracts message data, and schedules
    async processing. Returns empty TwiML immediately to acknowledge receipt.

    The actual message processing happens in the background to ensure
    quick webhook response times (< 200ms).
    """
    settings = get_settings()

    # Parse form data
    form_data = await request.form()
    params = {key: form_data[key] for key in form_data}

    # Validate Twilio signature
    if settings.ENABLE_SIGNATURE_VALIDATION:
        signature = request.headers.get("X-Twilio-Signature", "")

        # Construct the full URL for validation
        # Note: In Cloud Run, may need to use X-Forwarded-Proto header
        url = str(request.url)
        if request.headers.get("X-Forwarded-Proto") == "https":
            url = url.replace("http://", "https://")

        if not _validator.validate(url, params, signature):
            logger.warning(f"Invalid Twilio signature from {params.get('From', 'unknown')}")
            raise HTTPException(status_code=400, detail="Invalid signature")

    # Extract required fields
    from_raw = params.get("From")
    to_number = params.get("To")
    body = params.get("Body", "").strip()
    message_sid = params.get("MessageSid", "unknown")

    if not from_raw or not body:
        logger.warning(f"Missing required fields: From={from_raw}, Body={bool(body)}")
        raise HTTPException(status_code=400, detail="Missing required fields")

    # Parse channel and phone number from Twilio address
    channel, phone_number = MessageChannel.from_twilio_address(from_raw)

    logger.info(
        f"Received {channel.value}: MessageSid={message_sid}, From={phone_number}, "
        f"Body length={len(body)}"
    )

    # Schedule background processing
    background_tasks.add_task(
        process_message_async,
        phone_number=phone_number,
        message=body,
        message_sid=message_sid,
        channel=channel,
    )

    # Return empty TwiML immediately
    return Response(
        content=EMPTY_TWIML,
        media_type="application/xml",
    )


async def process_message_async(
    phone_number: str,
    message: str,
    message_sid: str,
    channel: MessageChannel = MessageChannel.SMS,
) -> None:
    """Process incoming message in the background.

    Handles opt-in flow:
    1. New users get opt-in request message
    2. YES -> opt in and welcome
    3. STOP -> opt out
    4. HELP -> send help message
    5. Only forward to agent if opted in

    For opted-in users:
    1. Gets or creates session for the phone number
    2. Sends message to Agent Engine
    3. Splits response into message segments
    4. Sends response message(s)
    5. Updates session activity timestamp
    """
    settings = get_settings()
    start_time = time.time()
    normalized_message = message.upper().strip()

    try:
        # First, get or create user record (not agent session)
        session_info = await _session_manager.get_or_create_user(phone_number)

        logger.info(
            f"Processing message for {phone_number}: "
            f"opted_in={session_info.opted_in}, "
            f"is_new_user={session_info.is_new_user}"
        )

        # Handle STOP keyword - always process regardless of opt-in status
        if normalized_message == "STOP":
            logger.info(f"User {phone_number} requested opt-out")
            await _session_manager.set_opted_out(phone_number)
            await _sms_sender.send(phone_number, OPT_OUT_MSG, channel=channel)
            return

        # Handle HELP keyword - always process regardless of opt-in status
        if normalized_message == "HELP":
            logger.info(f"User {phone_number} requested help")
            await _sms_sender.send(phone_number, HELP_MSG, channel=channel)
            return

        # New user - send opt-in request
        if session_info.is_new_user:
            logger.info(f"New user {phone_number} - sending opt-in request")
            await _sms_sender.send(phone_number, OPT_IN_REQUEST_MSG, channel=channel)
            return

        # User not opted in - check if they're opting in now
        if not session_info.opted_in:
            if normalized_message == "YES":
                logger.info(f"User {phone_number} opted in")
                await _session_manager.set_opted_in(phone_number)
                await _sms_sender.send(phone_number, OPT_IN_SUCCESS_MSG, channel=channel)
                return
            else:
                # Not opted in and didn't say YES
                logger.info(f"User {phone_number} not opted in, sending reminder")
                await _sms_sender.send(phone_number, NOT_OPTED_IN_MSG, channel=channel)
                return

        # Check rate limit before processing (opted-in users only)
        if _rate_limiter:
            is_allowed, count = await _rate_limiter.check_and_increment(phone_number)
            if not is_allowed:
                logger.warning(f"Rate limit exceeded for {phone_number}: {count} messages today")
                await _sms_sender.send(phone_number, RATE_LIMIT_MSG, channel=channel)
                return

        # User is opted in - get or create agent session and process message
        session_info = await _session_manager.get_or_create_session(phone_number)

        logger.info(
            f"Forwarding to agent for {phone_number}: "
            f"session={session_info.agent_session_id}, "
            f"is_new_session={session_info.is_new_session}"
        )

        # Query the agent with timeout
        t_before_agent = time.time()
        try:
            response = await _agent_client.send_message(
                user_id=phone_number,
                session_id=session_info.agent_session_id,
                message=message,
            )
        except asyncio.TimeoutError:
            logger.error(f"Agent timeout for {phone_number}")
            await _send_error_message(
                phone_number,
                "I'm taking longer than usual to respond. Please try again in a moment.",
                channel=channel,
            )
            return
        agent_ms = (time.time() - t_before_agent) * 1000

        if not response:
            logger.warning(f"Empty response from agent for {phone_number}")
            await _send_error_message(
                phone_number,
                "I couldn't generate a response. Please try again.",
                channel=channel,
            )
            return

        # Send response (sender handles message splitting if needed)
        t_before_send = time.time()
        await _sms_sender.send(phone_number, response, channel=channel)
        send_ms = (time.time() - t_before_send) * 1000

        # Update session activity with channel
        await _session_manager.update_last_activity(phone_number, channel=channel.value)

        total_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Processed {channel.value} {message_sid} in {total_ms:.0f}ms "
            f"[agent={agent_ms:.0f}ms, send={send_ms:.0f}ms]: "
            f"response sent to {phone_number} ({len(response)} chars)"
        )

    except Exception as e:
        logger.exception(f"Error processing message {message_sid}: {e}")
        await _send_error_message(
            phone_number,
            "Something went wrong. Please try again.",
            channel=channel,
        )


async def _send_error_message(
    phone_number: str,
    message: str,
    channel: MessageChannel = MessageChannel.SMS,
) -> None:
    """Send an error message to the user.

    Wrapped in try/except to prevent cascading failures.
    """
    try:
        await _sms_sender.send(phone_number, message, channel=channel)
    except Exception as e:
        logger.error(f"Failed to send error {channel.value} to {phone_number}: {e}")
