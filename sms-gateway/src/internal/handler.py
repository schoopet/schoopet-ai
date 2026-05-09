"""Internal service endpoints for async task communication.

These endpoints handle:
1. Task completion notifications from Task Worker — delivers directly to user
2. User notifications for scheduled reminders

All endpoints require authentication via OIDC or HMAC signatures.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import verify_internal_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# Global references (initialized by main.py)
_session_manager = None
_agent_client = None
_telegram_sender = None
_slack_sender = None
_discord_sender = None
_firestore_client = None


def init_internal_services(
    session_manager,
    agent_client,
    firestore_client=None,
    telegram_sender=None,
    slack_sender=None,
    discord_sender=None,
):
    """Initialize internal handler services.

    Called by main.py during application startup.
    """
    global _session_manager, _agent_client, _telegram_sender, _slack_sender, _discord_sender, _firestore_client
    _session_manager = session_manager
    _agent_client = agent_client
    _firestore_client = firestore_client
    _telegram_sender = telegram_sender
    _slack_sender = slack_sender
    _discord_sender = discord_sender
    logger.info("Internal handler services initialized")


# ========== Request Models ==========


class TaskReviewRequest(BaseModel):
    """Request payload from Task Worker when async task completes."""

    task_id: str = Field(..., description="Task ID")
    user_id: str = Field(..., description="User's phone number")
    result: Optional[str] = Field(default=None, description="Task result")
    error: Optional[str] = Field(default=None, description="Error if task failed")


class UserNotifyRequest(BaseModel):
    """Request payload for direct user notification (scheduled reminders)."""

    user_id: str = Field(..., description="User's phone number")
    task_id: str = Field(..., description="Task ID that was completed")
    message: str = Field(..., description="Message to send to user")
    channel: str = Field(default="discord", description="Notification channel (discord/telegram/slack)")
    notification_session_scope: str = Field(default="", description="Optional scoped session for notification")
    discord_channel_id: str = Field(default="", description="Optional Discord channel target")
    discord_channel_name: str = Field(default="", description="Optional Discord channel name")


class InternalResponse(BaseModel):
    """Standard response for internal endpoints."""

    status: str
    message: Optional[str] = None


# ========== Helpers ==========


async def _mark_task_notified(task_id: str) -> None:
    """Set task status to NOTIFIED after confirmed delivery."""
    if not _firestore_client or not task_id:
        return
    try:
        from datetime import datetime, timezone
        await _firestore_client.collection("async_tasks").document(task_id).update({
            "status": "notified",
            "notified_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.warning(f"Failed to mark task {task_id} as notified: {e}")


async def _send_direct_notification(
    user_id: str,
    message: str,
    channel: str,
    discord_channel_id: str = "",
) -> str:
    """Deliver without agent processing and return the concrete channel used."""
    if channel == "discord" and _discord_sender:
        if discord_channel_id and hasattr(_discord_sender, "send_channel"):
            try:
                await _discord_sender.send_channel(discord_channel_id, message)
                return "discord_channel"
            except Exception as e:
                logger.warning(
                    f"Discord channel delivery failed for {discord_channel_id}; "
                    f"falling back to DM: {e}"
                )
        await _discord_sender.send(user_id, message)
        return "discord_dm"
    if channel == "telegram" and _telegram_sender:
        await _telegram_sender.send(user_id, message)
        return "telegram"
    if channel == "slack" and _slack_sender:
        await _slack_sender.send(user_id, message)
        return "slack"
    logger.warning(f"No sender available for channel {channel!r}")
    return channel


def _format_agent_notification_message(
    message: str,
    channel: str,
    notification_session_scope: str = "",
    discord_channel_id: str = "",
    discord_channel_name: str = "",
) -> str:
    """Build the notification prompt sent into an active agent session."""
    if channel != "discord" or not (notification_session_scope or discord_channel_id or discord_channel_name):
        return f"INTERNAL_TASK_COMPLETE: {message}"

    lines = ["Discord context:"]
    if notification_session_scope:
        lines.append(f"session_scope: {notification_session_scope}")
    if discord_channel_id:
        lines.append(f"channel_id: {discord_channel_id}")
    if discord_channel_name:
        lines.append(f"channel_name: {discord_channel_name}")
    lines.extend(["", f"INTERNAL_TASK_COMPLETE: {message}"])
    return "\n".join(lines)


async def _deliver_notification(
    user_id: str,
    task_id: str,
    message: str,
    channel: str,
    notification_session_scope: str = "",
    discord_channel_id: str = "",
    discord_channel_name: str = "",
) -> str:
    """Send a message to the user and mark the task notified.

    If the user has an active session, routes through the agent so the
    message is delivered conversationally. Falls back to direct send if the
    agent times out, returns empty text, or raises confirmation requests
    (which require Discord button infrastructure unavailable here).

    Returns the delivery method used: 'session' or 'direct'.
    """
    user_session = await _session_manager.get_user_session(
        user_id,
        session_scope=notification_session_scope or None,
    )
    session_channel = user_session.channel if user_session else channel

    if user_session and _session_manager.is_session_active(user_session) and _agent_client:
        try:
            events = await _agent_client.send_message_events(
                user_id=user_id,
                session_id=user_session.agent_session_id,
                message=_format_agent_notification_message(
                    message,
                    channel=channel,
                    notification_session_scope=notification_session_scope,
                    discord_channel_id=discord_channel_id,
                    discord_channel_name=discord_channel_name,
                ),
            )
        except asyncio.TimeoutError:
            logger.warning(f"Agent timeout delivering task {task_id}, falling back to direct send")
            events = []

        confirmations = _agent_client.extract_confirmation_requests(events)
        if confirmations:
            logger.warning(
                f"Agent requested {len(confirmations)} confirmation(s) during notification for "
                f"task {task_id} — personal agent should not write during INTERNAL_TASK_COMPLETE; "
                f"falling back to direct send"
            )
            # Fall through to direct send (Discord button infrastructure not available here)
        else:
            agent_response = _agent_client.extract_text(events)
            if agent_response:
                # Use the session channel, but fall back to the task's channel for
                # non-interactive channels like 'email' that have no outbound sender.
                effective_channel = session_channel if session_channel in ("discord", "telegram", "slack") else channel
                if effective_channel == "discord" and _discord_sender:
                    await _send_direct_notification(
                        user_id,
                        agent_response,
                        effective_channel,
                        discord_channel_id=discord_channel_id,
                    )
                elif effective_channel == "telegram" and _telegram_sender:
                    await _telegram_sender.send(user_id, agent_response)
                elif effective_channel == "slack" and _slack_sender:
                    await _slack_sender.send(user_id, agent_response)
                else:
                    logger.warning(f"No sender available for channel {effective_channel!r} (session={session_channel!r})")
                logger.info(f"Notification sent via {effective_channel} after agent processing")
                await _mark_task_notified(task_id)
                return "session"
            else:
                logger.warning(
                    f"Agent returned empty response for task {task_id}; falling back to direct send"
                )
                # Fall through to direct send

    # No active session OR empty agent response OR confirmations blocked text — send directly
    direct_channel = await _send_direct_notification(
        user_id,
        message,
        channel,
        discord_channel_id=discord_channel_id,
    )
    logger.info(f"Notification sent directly via {direct_channel}")
    await _mark_task_notified(task_id)
    return "direct"


# ========== Endpoints ==========


@router.post("/task-review", response_model=InternalResponse)
async def trigger_task_review(
    request: Request,
    payload: TaskReviewRequest,
    caller: str = Depends(verify_internal_request),
):
    """Handle task completion from Task Worker — deliver result directly to user.

    Security: Requires valid OIDC token from an allowed service account.

    Flow:
    1. Task Worker completes async task execution
    2. Task Worker calls this endpoint with the result
    3. This endpoint fetches notification_channel from Firestore
    4. Delivers result directly to the user (via active session or direct send)
    """
    if not _session_manager or not _firestore_client:
        raise HTTPException(status_code=503, detail="Internal services not initialized")

    logger.info(
        f"Task completed, notifying user {payload.user_id} for task {payload.task_id} "
        f"(caller={caller})"
    )

    try:
        # Fetch notification_channel from Firestore task doc
        doc = await _firestore_client.collection("async_tasks").document(payload.task_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"Task {payload.task_id} not found")
        task_data = doc.to_dict()
        channel = task_data.get("notification_channel", "discord")
        notification_session_scope = task_data.get("notification_session_scope", "")
        discord_channel_id = task_data.get("discord_channel_id", "")
        discord_channel_name = task_data.get("discord_channel_name", "")

        if payload.error:
            message = f"Your background task encountered an error: {payload.error}"
        elif payload.result:
            message = payload.result
        else:
            logger.warning(f"Task {payload.task_id} completed with no result or error")
            return InternalResponse(status="skipped", message="No result to deliver")

        method = await _deliver_notification(
            user_id=payload.user_id,
            task_id=payload.task_id,
            message=message,
            channel=channel,
            notification_session_scope=notification_session_scope,
            discord_channel_id=discord_channel_id,
            discord_channel_name=discord_channel_name,
        )

        return InternalResponse(status="notified", message=f"Delivered via {method}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to notify user for task {payload.task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user-notify", response_model=InternalResponse)
async def notify_user(
    request: Request,
    payload: UserNotifyRequest,
    caller: str = Depends(verify_internal_request),
):
    """Send a direct notification to a user (used for scheduled reminders).

    Delivery method:
    - If user has active session: Send via root agent in user session
    - If no active session: Send directly via the channel sender

    Security: Requires valid OIDC token from an allowed service account.
    """
    if not _session_manager or (not _telegram_sender and not _slack_sender and not _discord_sender):
        raise HTTPException(status_code=503, detail="Internal services not initialized")

    logger.info(
        f"User notify triggered by {caller} for user {payload.user_id}, "
        f"task {payload.task_id}"
    )

    try:
        method = await _deliver_notification(
            user_id=payload.user_id,
            task_id=payload.task_id,
            message=payload.message,
            channel=payload.channel,
            notification_session_scope=payload.notification_session_scope,
            discord_channel_id=payload.discord_channel_id,
            discord_channel_name=payload.discord_channel_name,
        )
        return InternalResponse(status=f"notified_via_{method}", message=f"Delivered via {method}")

    except Exception as e:
        logger.error(f"Failed to notify user: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=InternalResponse)
async def internal_health():
    """Health check for internal endpoints.

    No authentication required - used for load balancer health checks.
    """
    return InternalResponse(
        status="healthy",
        message="Internal endpoints operational"
    )
