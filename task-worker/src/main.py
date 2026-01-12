"""FastAPI application for Task Worker service.

This service executes async tasks spawned by the root agent:
1. Receives task execution requests from Cloud Tasks
2. Executes tasks using specialized async agents
3. Notifies SMS Gateway for root agent review
4. Handles revisions when corrections are requested
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
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
    logger.info(f"Agent Engine: {os.getenv('AGENT_ENGINE_ID')}")
    logger.info(f"SMS Gateway: {os.getenv('SMS_GATEWAY_URL')}")

    # Initialize worker
    _worker = TaskWorker()

    logger.info("Task Worker started successfully")

    yield

    logger.info("Shutting down Task Worker...")


# Create FastAPI application
app = FastAPI(
    title="Shoopet Task Worker",
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


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Shoopet Task Worker",
        "status": "running",
    }
