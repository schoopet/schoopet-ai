"""Discord interaction webhook handler."""
import asyncio
import json
import logging
import time
from typing import Union

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from google.genai import types

from ..config import get_settings
from ..messages import RATE_LIMIT_MSG
from .context import (
    DiscordContext,
    build_discord_context,
    wrap_message_with_discord_context,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Discord interaction types
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
INTERACTION_MESSAGE_COMPONENT = 3

# Discord interaction response types
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4
RESPONSE_DEFERRED_CHANNEL_MESSAGE = 5
RESPONSE_UPDATE_MESSAGE = 7

EPHEMERAL_FLAG = 64

# Global references to services (initialized by main.py)
_validator = None
_session_manager = None
_agent_client = None
_discord_sender = None
_rate_limiter = None


def init_services(validator, session_manager, agent_client, discord_sender, rate_limiter=None):
    """Initialize service references for the Discord handler.

    Called by main.py during application startup.
    """
    global _validator, _session_manager, _agent_client, _discord_sender, _rate_limiter
    _validator = validator
    _session_manager = session_manager
    _agent_client = agent_client
    _discord_sender = discord_sender
    _rate_limiter = rate_limiter


def _context_from_interaction(data: dict) -> DiscordContext:
    channel_id = str(data.get("channel_id") or "")
    guild_id = str(data.get("guild_id") or "")
    channel_data = data.get("channel") or {}
    channel_name = str(channel_data.get("name") or "")
    return build_discord_context(
        channel_id=channel_id,
        guild_id=guild_id,
        channel_name=channel_name,
    )


async def _get_pending_confirmation(user_id: str, pending_id: str, session_scope: str = ""):
    if session_scope:
        return await _session_manager.get_pending_confirmation(
            user_id,
            pending_id,
            session_scope=session_scope,
        )
    return await _session_manager.get_pending_confirmation(user_id, pending_id)


async def _clear_pending_confirmation(user_id: str, pending_id: str, session_scope: str = ""):
    if session_scope:
        await _session_manager.clear_pending_confirmation(
            user_id,
            pending_id,
            session_scope=session_scope,
        )
        return
    await _session_manager.clear_pending_confirmation(user_id, pending_id)


@router.post("/webhook/discord")
async def handle_discord_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Handle incoming Discord interaction.

    Discord requires a response within 3 seconds. For agent queries we
    return a deferred response (type 5) immediately and send the real
    reply as a follow-up once the agent responds.

    Endpoint URL to configure in Discord Developer Portal:
        https://api.schoopet.com/webhook/discord
    """
    settings = get_settings()
    body = await request.body()

    # Validate Ed25519 signature (Discord rejects endpoints that skip this)
    if settings.ENABLE_SIGNATURE_VALIDATION:
        signature = request.headers.get("X-Signature-Ed25519", "")
        timestamp = request.headers.get("X-Signature-Timestamp", "")
        if not _validator or not _validator.validate(signature, timestamp, body):
            logger.warning("Invalid Discord request signature")
            raise HTTPException(status_code=401, detail="Invalid request signature")

    data = json.loads(body)
    interaction_type = data.get("type")

    # PING — Discord verifies the endpoint with this before accepting it
    if interaction_type == INTERACTION_PING:
        return JSONResponse({"type": RESPONSE_PONG})

    # Slash command (APPLICATION_COMMAND)
    if interaction_type == INTERACTION_APPLICATION_COMMAND:
        command = data.get("data", {}).get("name", "")
        if command != "chat":
            # Unknown command — acknowledge silently
            return JSONResponse({"type": RESPONSE_PONG})

        # Extract user ID (guild interaction has member.user; DMs have user)
        user_data = (data.get("member") or {}).get("user") or data.get("user") or {}
        user_id = str(user_data.get("id", ""))
        if not user_id:
            logger.warning("Discord interaction missing user ID")
            raise HTTPException(status_code=400, detail="Missing user ID")

        # Extract message text from the "message" option
        options = data.get("data", {}).get("options", [])
        text = next((o["value"] for o in options if o["name"] == "message"), "").strip()
        if not text:
            return JSONResponse({"type": RESPONSE_DEFERRED_CHANNEL_MESSAGE})

        interaction_token = data.get("token", "")
        discord_context = _context_from_interaction(data)

        logger.info(
            f"Received Discord /chat command: user={user_id}, "
            f"scope={discord_context.session_scope}, text length={len(text)}"
        )

        # Acknowledge immediately with a deferred response, then process in background
        background_tasks.add_task(
            process_discord_message,
            user_id=user_id,
            text=text,
            interaction_token=interaction_token,
            discord_context=discord_context,
        )
        return JSONResponse({"type": RESPONSE_DEFERRED_CHANNEL_MESSAGE})

    # Component clicks (Approve/Reject confirmation buttons)
    if interaction_type == INTERACTION_MESSAGE_COMPONENT:
        custom_id = str(data.get("data", {}).get("custom_id", ""))
        user_data = (data.get("member") or {}).get("user") or data.get("user") or {}
        user_id = str(user_data.get("id", ""))
        interaction_token = data.get("token", "")
        discord_context = _context_from_interaction(data)

        if not user_id or ":" not in custom_id:
            return JSONResponse({
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {
                    "content": "That approval request is invalid.",
                    "flags": EPHEMERAL_FLAG,
                },
            })

        pending_id, action = custom_id.split(":", 1)
        if action not in ("approve", "reject"):
            return JSONResponse({
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {
                    "content": "That approval action is invalid.",
                    "flags": EPHEMERAL_FLAG,
                },
            })

        pending = await _get_pending_confirmation(
            user_id,
            pending_id,
            session_scope=discord_context.session_scope,
        )
        if not pending:
            return JSONResponse({
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {
                    "content": "That approval is no longer pending, or it is not for you.",
                    "flags": EPHEMERAL_FLAG,
                },
            })

        confirmed = action == "approve"
        logger.info(
            f"Discord confirmation component: user={user_id}, "
            f"pending={pending_id}, confirmed={confirmed}"
        )
        background_tasks.add_task(
            process_discord_confirmation_component,
            user_id=user_id,
            pending_id=pending_id,
            interaction_token=interaction_token,
            confirmed=confirmed,
            session_scope=discord_context.session_scope,
        )

        content = "Approved. Working on it..." if confirmed else "Rejected. Working on it..."
        return JSONResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {
                "content": content,
                "components": [],
            },
        })

    # All other interaction types — acknowledge silently
    return JSONResponse({"type": RESPONSE_PONG})


async def process_discord_message(
    user_id: str,
    text: Union[str, types.Content],
    interaction_token: str,
    discord_context: DiscordContext | None = None,
) -> None:
    """Process a Discord /chat slash command in the background."""
    async def _reply(message: str) -> None:
        try:
            await _discord_sender.followup(interaction_token, message)
        except Exception as e:
            logger.error(f"Failed to send Discord followup to {user_id}: {e}")

    await _handle_discord_message(user_id, text, _reply, discord_context=discord_context)


async def process_discord_confirmation_component(
    user_id: str,
    pending_id: str,
    interaction_token: str,
    confirmed: bool,
    session_scope: str = "",
) -> None:
    """Resolve an ADK confirmation from a Discord component webhook."""
    try:
        pending = await _get_pending_confirmation(
            user_id,
            pending_id,
            session_scope=session_scope,
        )
        if not pending:
            await _discord_sender.send_followup(
                interaction_token,
                "That approval is no longer pending.",
            )
            return

        events = await _agent_client.send_confirmation_response(
            user_id=user_id,
            session_id=pending["agent_session_id"],
            confirmation_function_call_id=pending["adk_confirmation_function_call_id"],
            confirmed=confirmed,
        )
        await _clear_pending_confirmation(
            user_id,
            pending_id,
            session_scope=session_scope,
        )

        response = _agent_client.extract_text(events)
        if response:
            await _discord_sender.send_followup(interaction_token, response)
        await _session_manager.update_last_activity(
            user_id,
            channel="discord",
            session_scope=session_scope,
        )
    except Exception as e:
        logger.exception(f"Failed to resolve Discord component confirmation for {user_id}: {e}")
        try:
            await _discord_sender.send_followup(
                interaction_token,
                "I couldn't resolve that approval. Please try again.",
            )
        except Exception as send_err:
            logger.error(f"Failed to send Discord confirmation error followup: {send_err}")


async def _handle_discord_message(
    user_id: str,
    message: Union[str, types.Content],
    reply_fn,
    discord_context: DiscordContext | None = None,
) -> None:
    """Shared processing pipeline for all Discord message sources.

    Used by both the /chat slash command handler and the gateway DM/mention
    handler. The caller provides a reply_fn coroutine that delivers the
    response via the appropriate Discord mechanism.

    Flow:
    1. Check rate limit.
    2. Get or create agent session.
    3. Query the agent.
    4. Deliver reply via reply_fn.
    """
    start_time = time.time()
    discord_context = discord_context or build_discord_context(channel_id="")

    try:
        # Check rate limit
        if _rate_limiter:
            is_allowed, count = await _rate_limiter.check_and_increment(user_id)
            if not is_allowed:
                logger.warning(
                    f"Rate limit exceeded for Discord user {user_id}: {count} messages today"
                )
                await reply_fn(RATE_LIMIT_MSG)
                return

        # Get or create agent session
        session_info = await _session_manager.get_or_create_session(
            user_id,
            channel="discord",
            session_scope=discord_context.session_scope,
            state_extra=discord_context.state_extra,
        )
        logger.info(
            f"Forwarding to agent for Discord user {user_id}: "
            f"session={session_info.agent_session_id}, "
            f"scope={discord_context.session_scope}, "
            f"is_new_session={session_info.is_new_session}"
        )

        # Query the agent
        try:
            response = await _agent_client.send_message(
                user_id=user_id,
                session_id=session_info.agent_session_id,
                message=wrap_message_with_discord_context(message, discord_context),
            )
        except asyncio.TimeoutError:
            logger.error(f"Agent timeout for Discord user {user_id}")
            await reply_fn("I'm taking longer than usual to respond. Please try again in a moment.")
            return

        if not response:
            logger.warning(f"Empty response from agent for Discord user {user_id}")
            await reply_fn("I couldn't generate a response. Please try again.")
            return

        await reply_fn(response)
        await _session_manager.update_last_activity(
            user_id,
            channel="discord",
            session_scope=discord_context.session_scope,
        )

        processing_time = (time.time() - start_time) * 1000
        logger.info(
            f"Processed Discord message in {processing_time:.0f}ms: "
            f"response sent to {user_id} ({len(response)} chars)"
        )

    except Exception as e:
        logger.exception(f"Error processing Discord message for {user_id}: {e}")
        try:
            await reply_fn("Something went wrong. Please try again.")
        except Exception as send_err:
            logger.error(f"Failed to send error reply to Discord user {user_id}: {send_err}")
