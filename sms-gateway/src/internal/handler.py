"""Internal service endpoints for async task communication.

These endpoints handle:
1. Task review notifications from Task Worker
2. User notifications after task approval

All endpoints require authentication via OIDC or HMAC signatures.
"""
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
_sms_sender = None
_telegram_sender = None
_slack_sender = None
_discord_sender = None
_firestore_client = None


def init_internal_services(
    session_manager,
    agent_client,
    sms_sender,
    firestore_client=None,
    telegram_sender=None,
    slack_sender=None,
    discord_sender=None,
):
    """Initialize internal handler services.

    Called by main.py during application startup.
    """
    global _session_manager, _agent_client, _sms_sender, _telegram_sender, _slack_sender, _discord_sender, _firestore_client
    _session_manager = session_manager
    _agent_client = agent_client
    _sms_sender = sms_sender
    _firestore_client = firestore_client
    _telegram_sender = telegram_sender
    _slack_sender = slack_sender
    _discord_sender = discord_sender
    logger.info("Internal handler services initialized")


# ========== Request Models ==========


class TaskReviewRequest(BaseModel):
    """Request payload from Task Worker when async task completes."""

    task_id: str = Field(..., description="Task ID to review")
    user_id: str = Field(..., description="User's phone number")
    agent_type: str = Field(default="personal", description="Kept for backward compat, ignored")
    result: Optional[str] = Field(default=None, description="Task result")
    error: Optional[str] = Field(default=None, description="Error if task failed")


class UserNotifyRequest(BaseModel):
    """Request payload for user notification after task approval."""

    user_id: str = Field(..., description="User's phone number")
    task_id: str = Field(..., description="Task ID that was completed")
    message: str = Field(..., description="Message to send to user")
    channel: str = Field(default="sms", description="Notification channel (sms/whatsapp/telegram)")


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


# ========== Endpoints ==========


@router.post("/task-review", response_model=InternalResponse)
async def trigger_task_review(
    request: Request,
    payload: TaskReviewRequest,
    caller: str = Depends(verify_internal_request),
):
    """Handle task completion notification from Task Worker.

    When an async task completes, the Task Worker calls this endpoint
    to trigger root agent review in a supervisor session.

    Security: Requires valid OIDC token from an allowed service account.

    Flow:
    1. Task Worker completes async task execution
    2. Task Worker calls this endpoint with task result
    3. This endpoint creates/gets supervisor session for the task
    4. Sends internal message to root agent for review
    5. Root agent reviews and either approves or requests correction
    """
    if not _session_manager or not _agent_client:
        raise HTTPException(
            status_code=503,
            detail="Internal services not initialized"
        )

    logger.info(
        f"Task review triggered by {caller} for task {payload.task_id}, "
        f"user {payload.user_id}"
    )

    try:
        # Get or create supervisor session for this task
        supervisor_session = await _session_manager.get_supervisor_session(
            phone_number=payload.user_id,
            task_id=payload.task_id,
        )

        # Build internal review message for root agent
        review_message = _build_review_message(payload)

        response = await _agent_client.send_message(
            user_id=f"{payload.user_id}_supervisor",
            session_id=supervisor_session.agent_session_id,
            message=review_message,
        )

        logger.info(
            f"Review request sent to supervisor session {supervisor_session.agent_session_id}"
        )

        return InternalResponse(
            status="review_triggered",
            message=f"Review initiated in session {supervisor_session.agent_session_id}"
        )

    except Exception as e:
        logger.error(f"Failed to trigger task review: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user-notify", response_model=InternalResponse)
async def notify_user(
    request: Request,
    payload: UserNotifyRequest,
    caller: str = Depends(verify_internal_request),
):
    """Send notification to user after task approval.

    Called when:
    1. Root agent approves an async task result
    2. A scheduled notification/reminder triggers

    Delivery method:
    - If user has active session: Send via root agent in user session
    - If no active session: Send directly via SMS

    Security: Requires valid OIDC token from an allowed service account.
    """
    if not _session_manager or (not _sms_sender and not _telegram_sender and not _slack_sender and not _discord_sender):
        raise HTTPException(status_code=503, detail="Internal services not initialized")

    logger.info(
        f"User notify triggered by {caller} for user {payload.user_id}, "
        f"task {payload.task_id}"
    )

    try:
        # Check if user has an active session
        user_session = await _session_manager.get_user_session(payload.user_id)

        session_channel = user_session.channel if user_session else payload.channel

        if user_session and _session_manager.is_session_active(user_session) and _agent_client:
            # User has active session - send via root agent for conversational delivery
            internal_message = f"INTERNAL_TASK_COMPLETE: {payload.message}"

            agent_response = await _agent_client.send_message(
                user_id=payload.user_id,
                session_id=user_session.agent_session_id,
                message=internal_message,
            )
            logger.info(f"Agent response for task notification: {agent_response[:100]}...")

            if agent_response:
                from ..channel import MessageChannel
                if session_channel == "discord" and _discord_sender:
                    await _discord_sender.send(payload.user_id, agent_response)
                elif session_channel == "telegram" and _telegram_sender:
                    await _telegram_sender.send(payload.user_id, agent_response)
                elif session_channel == "slack" and _slack_sender:
                    await _slack_sender.send(payload.user_id, agent_response)
                elif _sms_sender:
                    channel = MessageChannel.WHATSAPP if session_channel == "whatsapp" else MessageChannel.SMS
                    await _sms_sender.send(
                        to_number=payload.user_id,
                        body=agent_response,
                        channel=channel,
                    )
                logger.info(f"Notification sent via {session_channel} after agent processing")
                await _mark_task_notified(payload.task_id)

            return InternalResponse(
                status="notified_via_session",
                message="User notified through active session"
            )

        # No active session - send directly
        from ..channel import MessageChannel
        if payload.channel == "discord" and _discord_sender:
            await _discord_sender.send(payload.user_id, payload.message)
        elif payload.channel == "telegram" and _telegram_sender:
            await _telegram_sender.send(payload.user_id, payload.message)
        elif payload.channel == "slack" and _slack_sender:
            await _slack_sender.send(payload.user_id, payload.message)
        elif _sms_sender:
            channel = MessageChannel.WHATSAPP if payload.channel == "whatsapp" else MessageChannel.SMS
            await _sms_sender.send(
                to_number=payload.user_id,
                body=payload.message,
                channel=channel,
            )

        logger.info(f"Notification sent directly via {payload.channel}")
        await _mark_task_notified(payload.task_id)

        return InternalResponse(
            status="notified_via_direct",
            message=f"User notified via {payload.channel}"
        )

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


# ========== Helper Functions ==========


def _build_review_message(payload: TaskReviewRequest) -> str:
    """Build the internal review message for root agent.

    The message includes a special prefix that the root agent recognizes
    as an internal task review request.
    """
    parts = [
        "INTERNAL_TASK_REVIEW",
        f"Task ID: {payload.task_id}",
        "",
    ]

    if payload.error:
        parts.append(f"Status: FAILED")
        parts.append(f"Error: {payload.error}")
    else:
        parts.append(f"Status: Awaiting Review")
        if payload.result:
            parts.append("")
            parts.append("Result:")
            parts.append(payload.result)

    parts.append("")
    parts.append("Use review_task_result(task_id) for full details.")
    parts.append("Then use approve_task(task_id) or request_correction(task_id, feedback).")
    parts.append("")
    parts.append("IMPORTANT: This notification is NOT shown to the user.")
    parts.append("You MUST notify the user of this notification and provide any information they should know.")

    return "\n".join(parts)
