"""Task Worker - Executes async tasks via deployed Agent Engine.

This module handles:
1. Loading task definitions from Firestore
2. Delegating execution to the deployed Agent Engine (full tool access)
3. Notifying SMS Gateway for root agent review
4. Processing revision requests
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Mirrors _RESOURCE_CONFIRMED_PREFIX in agents/schoopet/resource_confirmation.py.
# Both must stay in sync — changing one without the other silently breaks pre-auth.
_RESOURCE_CONFIRMED_PREFIX = "_resource_confirmed_"


def _build_allowed_resource_state(allowed_resource_ids: list) -> dict:
    """Build initial ADK session state that pre-approves specific resource IDs.

    Pre-populating these keys lets the task run without prompting the user for
    resources they already approved at scheduling time.
    """
    return {
        f"{_RESOURCE_CONFIRMED_PREFIX}{resource_id}": True
        for resource_id in allowed_resource_ids
    }


class TaskWorker:
    """Executes async tasks by delegating to the deployed Agent Engine."""

    def __init__(self):
        """Initialize the task worker - uses lazy initialization."""
        self._firestore_client = None
        self._vertex_client = None
        self._http_client = None
        self._initialized = False

        # Configuration
        self._project_id = None
        self._location = None
        self._personal_engine_id = None
        self._sms_gateway_url = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration and clients."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self._personal_engine_id = (
            os.getenv("PERSONAL_AGENT_ENGINE_ID") or os.getenv("AGENT_ENGINE_ID")
        )
        self._sms_gateway_url = os.getenv("SMS_GATEWAY_URL")

        self._initialized = True

        if not self._project_id or not self._personal_engine_id:
            logger.error("Missing required environment variables")

    def _get_firestore_client(self):
        """Get Firestore client, initializing lazily."""
        if self._firestore_client is None:
            self._ensure_initialized()
            if self._project_id:
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=self._project_id)
        return self._firestore_client

    def _get_vertex_client(self):
        """Get Vertex AI client, initializing lazily."""
        if self._vertex_client is None:
            self._ensure_initialized()
            if self._project_id:
                from vertexai import Client
                self._vertex_client = Client(
                    project=self._project_id,
                    location=self._location
                )
        return self._vertex_client

    async def _get_http_client(self):
        """Get HTTP client for internal calls."""
        if self._http_client is None:
            import aiohttp
            self._http_client = aiohttp.ClientSession()
        return self._http_client

    async def execute_task(self, task_id: str) -> Dict[str, Any]:
        """Execute an async task.

        Args:
            task_id: The Firestore task document ID

        Returns:
            Dict with success status and any error message
        """
        self._ensure_initialized()

        # Atomically claim the task before executing it so Cloud Tasks retries
        # or duplicate deliveries cannot run the same Firestore task twice.
        claim = await self._claim_task_for_execution(task_id)
        if claim is None:
            return {"success": False, "error": f"Task {task_id} not found"}

        task = claim["task"]
        prior_status = claim["prior_status"]
        if not claim["claimed"]:
            logger.info(f"Task {task_id} already processed: {prior_status}")
            return {"success": True, "message": f"Task already in status: {prior_status}"}

        try:
            prompt = self._build_task_prompt(task)
            result = await self._execute_task(task, prompt)

            # Update task with results
            await self._update_task_result(
                task_id=task_id,
                result=result,
                status="completed"
            )

            # Notify SMS Gateway to deliver result to user
            await self._notify_user(
                task_id=task_id,
                user_id=task["user_id"],
                result=result
            )

            logger.info(f"Task {task_id} completed successfully")
            return {"success": True}

        except Exception as e:
            logger.exception(f"Task {task_id} failed: {e}")

            # Update task with error
            await self._update_task_error(task_id, str(e))

            # Notify about failure
            await self._notify_user(
                task_id=task_id,
                user_id=task["user_id"],
                result=None,
                error=str(e)
            )

            return {"success": False, "error": str(e)}

    async def _get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task document from Firestore."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return None

        doc = firestore_client.collection("async_tasks").document(task_id).get()
        if doc.exists:
            return doc.to_dict()
        return None

    async def _claim_task_for_execution(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Claim a task for execution with an optimistic concurrency check."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return None

        from google.cloud import firestore

        doc_ref = firestore_client.collection("async_tasks").document(task_id)
        doc = doc_ref.get()
        if not doc.exists:
            return None

        task = doc.to_dict()
        status = task.get("status")
        if status not in ["pending", "scheduled"]:
            return {"claimed": False, "prior_status": status, "task": task}

        started_at = datetime.now(timezone.utc)
        try:
            doc_ref.update(
                {
                    "status": "running",
                    "started_at": started_at,
                },
                option=firestore.LastUpdateOption(doc.update_time),
            )
        except Exception as exc:
            logger.info(f"Task {task_id} claim lost due to concurrent update: {exc}")
            latest = doc_ref.get()
            latest_task = latest.to_dict() if latest.exists else task
            latest_status = latest_task.get("status") if latest_task else status
            return {
                "claimed": False,
                "prior_status": latest_status,
                "task": latest_task,
            }

        task["status"] = "running"
        task["started_at"] = started_at
        return {"claimed": True, "prior_status": status, "task": task}

    async def _update_task_status(self, task_id: str, status: str):
        """Update task status in Firestore."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return

        update_data = {
            "status": status,
        }

        if status == "running":
            update_data["started_at"] = datetime.now(timezone.utc)

        firestore_client.collection("async_tasks").document(task_id).update(update_data)

    async def _update_task_result(
        self,
        task_id: str,
        result: str,
        status: str
    ):
        """Update task with execution result."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return

        firestore_client.collection("async_tasks").document(task_id).update({
            "status": status,
            "result": result,
            "completed_at": datetime.now(timezone.utc),
        })

    async def _update_task_error(self, task_id: str, error: str):
        """Update task with error."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return

        firestore_client.collection("async_tasks").document(task_id).update({
            "status": "failed",
            "error": error,
            "completed_at": datetime.now(timezone.utc),
        })

    async def _execute_task(self, task: Dict[str, Any], prompt: str) -> str:
        """Execute a task by delegating to the deployed Agent Engine.

        Creates a session on the appropriate engine (personal/team),
        sends the prompt, and streams the result. The agent engine has
        full tool access (calendar, search, drive, sheets, memory, etc.).

        Memory continuity:
        - ADK handles memory reads inside the agent via built-in memory tools.
        - Memory creation is handled inside the agent via ADK callbacks.
        - After execution, the task session is deleted for cleanup.
        """
        engine_id = self._personal_engine_id

        if not engine_id:
            raise ValueError(
                "No engine ID configured. Set PERSONAL_AGENT_ENGINE_ID."
            )

        client = self._get_vertex_client()
        engine_name = (
            f"projects/{self._project_id}/locations/{self._location}"
            f"/reasoningEngines/{engine_id}"
        )

        adk_app = client.agent_engines.get(name=engine_name)
        user_id = task["user_id"]

        initial_state = _build_allowed_resource_state(task.get("allowed_resource_ids", {}))
        session = await adk_app.async_create_session(user_id=user_id, state=initial_state)
        session_id = session["id"]

        logger.info(
            f"Created Agent Engine session {session_id} on engine {engine_id} "
            f"for user {user_id}"
        )

        try:
            # Stream the response
            result_parts = []
            async for event in adk_app.async_stream_query(
                user_id=user_id,
                session_id=session_id,
                message=prompt,
            ):
                if isinstance(event, dict):
                    content = event.get("content", {})
                    if isinstance(content, dict):
                        for part in content.get("parts", []):
                            if isinstance(part, dict) and "text" in part:
                                result_parts.append(part["text"])
                elif hasattr(event, "content") and event.content:
                    if hasattr(event.content, "parts"):
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                result_parts.append(part.text)

            result = "".join(result_parts)
            logger.info(f"Task execution complete: {len(result)} chars")
            return result
        finally:
            await self._retire_session(adk_app, user_id, session_id)

    async def _retire_session(self, adk_app, user_id: str, session_id: str) -> None:
        """Delete a finished task session."""
        try:
            await adk_app.async_delete_session(user_id=user_id, session_id=session_id)
            logger.info(f"Deleted task session {session_id}")
        except Exception as e:
            logger.warning(f"Failed to delete task session {session_id}: {e}")

    def _build_task_prompt(self, task: Dict[str, Any]) -> str:
        """Build the execution prompt for the async agent."""
        parts = [
            f"Execute this {task['task_type']} task:",
            "",
            "Instruction:",
            task["instruction"],
        ]

        if task.get("context"):
            parts.append("")
            parts.append("Additional context:")
            for key, value in task["context"].items():
                parts.append(f"  {key}: {value}")

        parts.append("")
        parts.append("Provide a clear, concise result.")

        return "\n".join(parts)

    async def _notify_user(
        self,
        task_id: str,
        user_id: str,
        result: Optional[str],
        error: Optional[str] = None
    ):
        """Notify SMS Gateway to deliver the task result to the user.

        Sends request to /internal/task-review endpoint.
        """
        if not self._sms_gateway_url:
            logger.error("SMS_GATEWAY_URL not configured")
            return

        try:
            http_client = await self._get_http_client()

            # Build payload
            payload = {
                "task_id": task_id,
                "user_id": user_id,
                "result": result,
                "error": error,
            }

            # Get OIDC token for authentication
            headers = {
                "Content-Type": "application/json",
            }

            # Add OIDC token if available
            token = await self._get_oidc_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

            url = f"{self._sms_gateway_url}/internal/task-review"

            async with http_client.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    logger.info(f"User notification sent for task {task_id}")
                else:
                    text = await response.text()
                    logger.error(f"User notification failed: {response.status} - {text}")

        except Exception as e:
            logger.error(f"Failed to notify user: {e}")

    async def requeue_scheduled_tasks(self) -> Dict[str, int]:
        """Create Cloud Tasks for scheduled tasks entering the 30-day window.

        Queries Firestore for tasks with status="scheduled", scheduled_at within
        the next 720 hours, and no cloud_task_name yet, then creates Cloud Tasks
        for each. Called weekly by Cloud Scheduler.

        Returns:
            Dict with "queued" and "errors" counts.
        """
        self._ensure_initialized()
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            logger.error("Firestore not available for requeue")
            return {"queued": 0, "errors": 0}

        now = datetime.now(timezone.utc)
        window_end = now + timedelta(hours=720)

        docs = (
            firestore_client.collection("async_tasks")
            .where("status", "==", "scheduled")
            .where("scheduled_at", "<=", window_end)
            .get()
        )

        # Filter in Python — avoids a composite index on cloud_task_name
        pending = [d for d in docs if not d.to_dict().get("cloud_task_name")]

        queued = 0
        errors = 0

        for doc in pending:
            data = doc.to_dict()
            task_id = data["task_id"]
            user_id = data["user_id"]
            scheduled_at = data.get("scheduled_at")

            if not scheduled_at:
                logger.warning(f"Task {task_id} has no scheduled_at, skipping")
                errors += 1
                continue

            cloud_task_name = self._create_cloud_task(
                task_id=task_id,
                user_id=user_id,
                schedule_time=scheduled_at,
            )

            if cloud_task_name:
                firestore_client.collection("async_tasks").document(task_id).update(
                    {"cloud_task_name": cloud_task_name}
                )
                logger.info(f"Requeued task {task_id} → {cloud_task_name}")
                queued += 1
            else:
                errors += 1

        logger.info(f"Requeue complete: {queued} queued, {errors} errors")
        return {"queued": queued, "errors": errors}

    def _create_cloud_task(
        self,
        task_id: str,
        user_id: str,
        schedule_time: datetime,
    ) -> Optional[str]:
        """Create a single Cloud Task for a scheduled async task."""
        self._ensure_initialized()

        worker_url = os.getenv("TASK_WORKER_URL")
        queue = os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks")
        service_account = os.getenv(
            "TASK_WORKER_SA",
            f"task-worker@{self._project_id}.iam.gserviceaccount.com"
            if self._project_id
            else None,
        )

        if not worker_url or not self._project_id:
            logger.error("TASK_WORKER_URL or GOOGLE_CLOUD_PROJECT not set")
            return None

        from google.cloud import tasks_v2
        from google.api_core.exceptions import AlreadyExists
        from google.protobuf import duration_pb2, timestamp_pb2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(self._project_id, self._location, queue)

        normalized = re.sub(r"[^a-zA-Z0-9-]", "-", task_id).strip("-").lower()
        task_name = client.task_path(
            self._project_id, self._location, queue, f"execute-{normalized}-initial"
        )

        if schedule_time.tzinfo is None:
            schedule_time = schedule_time.replace(tzinfo=timezone.utc)
        ts = timestamp_pb2.Timestamp()
        ts.FromDatetime(schedule_time)

        task = {
            "name": task_name,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{worker_url}/execute",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"task_id": task_id, "user_id": user_id}).encode(),
                "oidc_token": {
                    "service_account_email": service_account,
                    "audience": worker_url,
                },
            },
            "schedule_time": ts,
            "dispatch_deadline": duration_pb2.Duration(seconds=900),
        }

        try:
            response = client.create_task(parent=parent, task=task)
            return response.name
        except AlreadyExists:
            logger.info(f"Cloud Task already exists for {task_id}, reusing name")
            return task_name
        except Exception as e:
            logger.error(f"Failed to create Cloud Task for {task_id}: {e}")
            return None

    async def _get_oidc_token(self) -> Optional[str]:
        """Get OIDC token for service-to-service auth.

        On Cloud Run, fetches ID token from the metadata server.
        """
        if not self._sms_gateway_url:
            logger.warning("SMS_GATEWAY_URL not set, cannot get OIDC token")
            return None

        try:
            # On Cloud Run, fetch ID token from metadata server
            metadata_url = (
                "http://metadata.google.internal/computeMetadata/v1/"
                f"instance/service-accounts/default/identity?audience={self._sms_gateway_url}"
            )

            http_client = await self._get_http_client()
            async with http_client.get(
                metadata_url,
                headers={"Metadata-Flavor": "Google"}
            ) as response:
                if response.status == 200:
                    token = await response.text()
                    logger.debug("Successfully obtained OIDC token from metadata server")
                    return token
                else:
                    text = await response.text()
                    logger.warning(f"Failed to get ID token from metadata: {response.status} - {text}")
                    return None

        except Exception as e:
            logger.warning(f"Could not get OIDC token: {e}")
            return None
