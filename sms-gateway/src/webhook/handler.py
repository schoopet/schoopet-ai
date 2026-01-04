"""Twilio webhook handler for incoming SMS messages."""
import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from ..config import get_settings
from ..sms.splitter import SMSSplitter

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
    "Shoopet SMS Assistant: Send a message to get started. "
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
_sms_splitter = SMSSplitter()


def init_services(validator, session_manager, agent_client, sms_sender):
    """Initialize service references for the webhook handler.

    Called by main.py during application startup.
    """
    global _validator, _session_manager, _agent_client, _sms_sender
    _validator = validator
    _session_manager = session_manager
    _agent_client = agent_client
    _sms_sender = sms_sender


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
    from_number = params.get("From")
    to_number = params.get("To")
    body = params.get("Body", "").strip()
    message_sid = params.get("MessageSid", "unknown")

    if not from_number or not body:
        logger.warning(f"Missing required fields: From={from_number}, Body={bool(body)}")
        raise HTTPException(status_code=400, detail="Missing required fields")

    logger.info(
        f"Received SMS: MessageSid={message_sid}, From={from_number}, "
        f"Body length={len(body)}"
    )

    # Schedule background processing
    background_tasks.add_task(
        process_message_async,
        phone_number=from_number,
        message=body,
        message_sid=message_sid,
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
    3. Splits response into SMS segments
    4. Sends response SMS(es)
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
            await _sms_sender.send(phone_number, OPT_OUT_MSG)
            return

        # Handle HELP keyword - always process regardless of opt-in status
        if normalized_message == "HELP":
            logger.info(f"User {phone_number} requested help")
            await _sms_sender.send(phone_number, HELP_MSG)
            return

        # New user - send opt-in request
        if session_info.is_new_user:
            logger.info(f"New user {phone_number} - sending opt-in request")
            await _sms_sender.send(phone_number, OPT_IN_REQUEST_MSG)
            return

        # User not opted in - check if they're opting in now
        if not session_info.opted_in:
            if normalized_message == "YES":
                logger.info(f"User {phone_number} opted in")
                await _session_manager.set_opted_in(phone_number)
                await _sms_sender.send(phone_number, OPT_IN_SUCCESS_MSG)
                return
            else:
                # Not opted in and didn't say YES
                logger.info(f"User {phone_number} not opted in, sending reminder")
                await _sms_sender.send(phone_number, NOT_OPTED_IN_MSG)
                return

        # User is opted in - get or create agent session and process message
        session_info = await _session_manager.get_or_create_session(phone_number)

        logger.info(
            f"Forwarding to agent for {phone_number}: "
            f"session={session_info.agent_session_id}, "
            f"is_new_session={session_info.is_new_session}"
        )

        # Query the agent with timeout
        try:
            response = await _agent_client.send_message(
                user_id=phone_number,
                session_id=session_info.agent_session_id,
                message=message,
            )
        except asyncio.TimeoutError:
            logger.error(f"Agent timeout for {phone_number}")
            await _send_error_sms(
                phone_number,
                "I'm taking longer than usual to respond. Please try again in a moment.",
            )
            return

        if not response:
            logger.warning(f"Empty response from agent for {phone_number}")
            await _send_error_sms(
                phone_number,
                "I couldn't generate a response. Please try again.",
            )
            return

        # Split response into SMS segments
        segments = _sms_splitter.split(response, max_segments=settings.MAX_SMS_SEGMENTS)

        if not segments:
            logger.warning(f"No segments generated for {phone_number}")
            return

        # Send response SMS(es)
        await _sms_sender.send_multi(phone_number, segments)

        # Update session activity
        await _session_manager.update_last_activity(phone_number)

        processing_time = (time.time() - start_time) * 1000
        logger.info(
            f"Processed message {message_sid} in {processing_time:.0f}ms: "
            f"{len(segments)} SMS segments sent to {phone_number}"
        )

    except Exception as e:
        logger.exception(f"Error processing message {message_sid}: {e}")
        await _send_error_sms(
            phone_number,
            "Something went wrong. Please try again.",
        )


async def _send_error_sms(phone_number: str, message: str) -> None:
    """Send an error message to the user.

    Wrapped in try/except to prevent cascading failures.
    """
    try:
        await _sms_sender.send(phone_number, message)
    except Exception as e:
        logger.error(f"Failed to send error SMS to {phone_number}: {e}")
