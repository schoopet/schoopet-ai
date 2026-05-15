"""Discord interaction webhook handler."""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..messages import RATE_LIMIT_MSG
from .context import (
    DiscordContext,
    build_discord_context,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Discord interaction types
INTERACTION_PING = 1
INTERACTION_MESSAGE_COMPONENT = 3

# Discord interaction response types
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4
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

        pending = await _session_manager.get_pending_approval(
            user_id,
            pending_id,
            session_scope=discord_context.session_scope or None,
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


async def process_discord_confirmation_component(
    user_id: str,
    pending_id: str,
    interaction_token: str,
    confirmed: bool,
    session_scope: str = "",
) -> None:
    """Resolve an ADK confirmation from a Discord component webhook."""
    try:
        pending = await _session_manager.get_pending_approval(
            user_id,
            pending_id,
            session_scope=session_scope or None,
        )
        if not pending:
            await _discord_sender.send_followup(
                interaction_token,
                "That approval is no longer pending.",
            )
            return

        pending_group = await _session_manager.get_pending_approval_group(
            user_id,
            pending_id,
            session_scope=session_scope or None,
        )
        if not pending_group:
            pending_group = [pending]

        all_events = []
        for grouped_pending in pending_group:
            events = await _agent_client.send_confirmation_response(
                user_id=user_id,
                session_id=grouped_pending["agent_session_id"],
                confirmation_function_call_id=grouped_pending["adk_confirmation_function_call_id"],
                confirmed=confirmed,
            )
            all_events.extend(events)

        await _session_manager.clear_pending_approval_group(
            user_id,
            pending_id,
            session_scope=session_scope or None,
        )

        response = _agent_client.extract_text(all_events)
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


