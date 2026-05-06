"""FastAPI application for Task Worker service.

This service executes async tasks spawned by the root agent:
1. Receives task execution requests from Cloud Tasks
2. Executes tasks using specialized async agents
3. Notifies SMS Gateway for root agent review
4. Handles revisions when corrections are requested
"""
import asyncio
import functools
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .worker import TaskWorker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Global worker instance
_worker: TaskWorker = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global _worker

    logger.info("Starting Task Worker...")
    logger.info(f"Project: {os.getenv('GOOGLE_CLOUD_PROJECT')}")
    logger.info(f"Personal Agent Engine: {os.getenv('PERSONAL_AGENT_ENGINE_ID') or '(not configured)'}")
    logger.info(f"SMS Gateway: {os.getenv('SMS_GATEWAY_URL')}")

    # Initialize worker
    _worker = TaskWorker()

    logger.info("Task Worker started successfully")

    yield

    logger.info("Shutting down Task Worker...")


# Create FastAPI application
app = FastAPI(
    title="Schoopet Task Worker",
    description="Async agent task execution service",
    version="1.0.0",
    lifespan=lifespan,
)


# ========== Request Models ==========


class ExecuteRequest(BaseModel):
    """Request payload from Cloud Tasks."""

    task_id: str = Field(..., description="Task ID to execute")
    user_id: str = Field(..., description="User's phone number")


class ExecuteResponse(BaseModel):
    """Response for task execution."""

    status: str
    message: str = None


# ========== Endpoints ==========


@app.post("/execute", response_model=ExecuteResponse)
async def execute_task(request: Request, payload: ExecuteRequest):
    """Execute an async task.

    Called by Cloud Tasks when a task needs to be executed.
    Handles both initial execution and revisions.

    Security: Cloud Tasks includes OIDC token for authentication.
    """
    if not _worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    logger.info(f"Executing task {payload.task_id} for user {payload.user_id}")

    try:
        result = await _worker.execute_task(payload.task_id)

        if result.get("success"):
            return ExecuteResponse(
                status="completed",
                message=f"Task executed successfully"
            )
        else:
            return ExecuteResponse(
                status="failed",
                message=result.get("error", "Unknown error")
            )

    except Exception as e:
        logger.error(f"Task execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _verify_scheduler_token(authorization: Optional[str]) -> None:
    """Verify that the caller is the task-requeue Cloud Scheduler SA."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication")

    token = authorization[7:]
    scheduler_sa = os.getenv("TASK_REQUEUE_SCHEDULER_SA", "")
    audience = os.getenv("TASK_WORKER_URL", "")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        loop = asyncio.get_running_loop()
        claims = await loop.run_in_executor(
            None,
            functools.partial(
                id_token.verify_oauth2_token,
                token,
                google_requests.Request(),
                audience=audience if audience else None,
            ),
        )

        if scheduler_sa and claims.get("email") != scheduler_sa:
            logger.warning(f"Unauthorized requeue caller: {claims.get('email')}")
            raise HTTPException(status_code=403, detail="Unauthorized")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scheduler token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/requeue-scheduled-tasks")
async def requeue_scheduled_tasks(
    authorization: Optional[str] = Header(None),
):
    """Queue Cloud Tasks for scheduled tasks entering the 30-day window.

    Called weekly by Cloud Scheduler. Finds Firestore tasks with
    status='scheduled', scheduled_at within the next 720 hours, and no
    cloud_task_name, then creates Cloud Tasks for each.
    """
    if not _worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    await _verify_scheduler_token(authorization)

    result = await _worker.requeue_scheduled_tasks()
    logger.info(f"Requeue run: {result}")
    return result


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Schoopet Task Worker",
        "status": "running",
    }
