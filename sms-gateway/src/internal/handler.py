"""Internal service endpoints for async task communication.

These endpoints handle:
1. Cloud Tasks execution for background Agent Engine tasks

All endpoints require authentication via OIDC or HMAC signatures.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import verify_internal_request
from .task_executor import GatewayTaskExecutor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# Global references (initialized by main.py)
_agent_client = None
_discord_sender = None
_firestore_client = None
_task_executor = None


def init_internal_services(
    agent_client,
    firestore_client=None,
    discord_sender=None,
):
    """Initialize internal handler services.

    Called by main.py during application startup.
    """
    global _agent_client, _discord_sender, _firestore_client, _task_executor
    _agent_client = agent_client
    _firestore_client = firestore_client
    _discord_sender = discord_sender
    _task_executor = (
        GatewayTaskExecutor(
            firestore_client=firestore_client,
            agent_client=agent_client,
            discord_sender=discord_sender,
        )
        if firestore_client and agent_client
        else None
    )
    logger.info("Internal handler services initialized")


# ========== Request Models ==========


class ExecuteTaskRequest(BaseModel):
    """Request payload from Cloud Tasks."""

    task_id: str = Field(..., description="Task ID to execute")
    user_id: str = Field(..., description="User ID that owns the task")


class InternalResponse(BaseModel):
    """Standard response for internal endpoints."""

    status: str
    message: Optional[str] = None


# ========== Endpoints ==========


@router.post("/tasks/execute", response_model=InternalResponse)
async def execute_task(
    request: Request,
    payload: ExecuteTaskRequest,
    caller: str = Depends(verify_internal_request),
):
    """Execute an async task from Cloud Tasks inside the gateway."""
    if not _task_executor:
        raise HTTPException(status_code=503, detail="Task executor not initialized")

    logger.info(
        "Executing gateway task %s for user %s (caller=%s)",
        payload.task_id,
        payload.user_id,
        caller,
    )

    result = await _task_executor.execute_task(payload.task_id)
    if result.get("success"):
        return InternalResponse(
            status="completed",
            message=result.get("message") or "Task executed successfully",
        )

    return InternalResponse(
        status="failed",
        message=result.get("error", "Unknown error"),
    )


@router.post("/tasks/requeue-scheduled", response_model=dict)
async def requeue_scheduled_tasks(
    request: Request,
    caller: str = Depends(verify_internal_request),
):
    """Queue Cloud Tasks for scheduled tasks entering the 30-day window."""
    if not _task_executor:
        raise HTTPException(status_code=503, detail="Task executor not initialized")

    result = await _task_executor.requeue_scheduled_tasks()
    logger.info("Task requeue triggered by %s: %s", caller, result)
    return result


@router.get("/health", response_model=InternalResponse)
async def internal_health():
    """Health check for internal endpoints.

    No authentication required - used for load balancer health checks.
    """
    return InternalResponse(
        status="healthy",
        message="Internal endpoints operational"
    )
