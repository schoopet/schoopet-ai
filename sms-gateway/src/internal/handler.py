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


def init_internal_services(session_manager, agent_client, sms_sender):
    """Initialize internal handler services.

    Called by main.py during application startup.
    """
    global _session_manager, _agent_client, _sms_sender
    _session_manager = session_manager
    _agent_client = agent_client
    _sms_sender = sms_sender
    logger.info("Internal handler services initialized")


# ========== Request Models ==========


class TaskReviewRequest(BaseModel):
    """Request payload from Task Worker when async task completes."""

    task_id: str = Field(..., description="Task ID to review")
    user_id: str = Field(..., description="User's phone number")
    result: Optional[str] = Field(default=None, description="Task result")
    error: Optional[str] = Field(default=None, description="Error if task failed")


class UserNotifyRequest(BaseModel):
    """Request payload for user notification after task approval."""

    user_id: str = Field(..., description="User's phone number")
    task_id: str = Field(..., description="Task ID that was completed")
    message: str = Field(..., description="Message to send to user")
    channel: str = Field(default="sms", description="Notification channel (sms/whatsapp)")


class InternalResponse(BaseModel):
    """Standard response for internal endpoints."""

    status: str
    message: Optional[str] = None


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

    Security: Requires valid OIDC token or HMAC signature.

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

        # Send message to root agent in supervisor session
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

    Security: Requires valid OIDC token or HMAC signature.
    """
    if not _session_manager or not _sms_sender:
        raise HTTPException(
            status_code=503,
            detail="Internal services not initialized"
        )

    logger.info(
        f"User notify triggered by {caller} for user {payload.user_id}, "
        f"task {payload.task_id}"
    )

    try:
        # Check if user has an active session
        user_session = await _session_manager.get_user_session(payload.user_id)

        if user_session and _session_manager.is_session_active(user_session) and _agent_client:
            # User has active session - send via root agent
            # This allows for conversational delivery
            internal_message = f"INTERNAL_TASK_COMPLETE: {payload.message}"

            agent_response = await _agent_client.send_message(
                user_id=payload.user_id,
                session_id=user_session.agent_session_id,
                message=internal_message,
            )
            logger.info(f"Agent response for task notification: {agent_response[:100]}...")

            # Send the agent's response to the user via the same channel as their session
            if agent_response and _sms_sender:
                from ..channel import MessageChannel
                # Use the session's channel (from user's last message), not the payload default
                session_channel = user_session.channel if user_session.channel else "sms"
                channel = MessageChannel.WHATSAPP if session_channel == "whatsapp" else MessageChannel.SMS

                await _sms_sender.send(
                    to_number=payload.user_id,
                    body=agent_response,
                    channel=channel,
                )
                logger.info(f"Notification sent via {session_channel} after agent processing")

            return InternalResponse(
                status="notified_via_session",
                message="User notified through active session"
            )

        # No active session or agent client - send directly via SMS
        from ..channel import MessageChannel

        channel = MessageChannel.WHATSAPP if payload.channel == "whatsapp" else MessageChannel.SMS

        await _sms_sender.send(
            to_number=payload.user_id,
            body=payload.message,
            channel=channel,
        )

        logger.info(f"Notification sent directly via {payload.channel}")

        return InternalResponse(
            status="notified_via_sms",
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
