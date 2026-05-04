"""Task Worker - Executes async tasks via deployed Agent Engine.

This module handles:
1. Loading task definitions from Firestore
2. Delegating execution to the deployed Agent Engine (full tool access)
3. Notifying SMS Gateway for root agent review
4. Processing revision requests
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
            # Execute via deployed Agent Engine (full tool access)
            if prior_status == "revision_requested":
                prompt = self._build_revision_prompt(task)
            else:
                prompt = self._build_task_prompt(task)

            result = await self._execute_task(task, prompt)

            # Update task with results
            await self._update_task_result(
                task_id=task_id,
                result=result,
                status="awaiting_review"
            )

            # Notify SMS Gateway for root agent review
            await self._notify_for_review(
                task_id=task_id,
                user_id=task["user_id"],
                agent_type=task.get("agent_type", "personal"),
                result=result
            )

            logger.info(f"Task {task_id} completed successfully")
            return {"success": True}

        except Exception as e:
            logger.exception(f"Task {task_id} failed: {e}")

            # Update task with error
            await self._update_task_error(task_id, str(e))

            # Notify about failure
            await self._notify_for_review(
                task_id=task_id,
                user_id=task["user_id"],
                agent_type=task.get("agent_type", "personal"),
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
        if status not in ["pending", "scheduled", "revision_requested"]:
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

    def _build_revision_prompt(self, task: Dict[str, Any]) -> str:
        """Build the revision prompt with feedback."""
        parts = [
            f"Revise this {task['task_type']} task based on feedback:",
            "",
            "Original instruction:",
            task["instruction"],
            "",
            "Previous result:",
            task.get("result", "(no previous result)"),
            "",
            "Revision feedback:",
            task.get("revision_feedback", "(no feedback)"),
            "",
            "Provide an improved result that addresses the feedback.",
        ]

        return "\n".join(parts)

    async def _notify_for_review(
        self,
        task_id: str,
        user_id: str,
        agent_type: str,
        result: Optional[str],
        error: Optional[str] = None
    ):
        """Notify SMS Gateway for root agent review.

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
                "agent_type": agent_type,
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
                    logger.info(f"Review notification sent for task {task_id}")
                else:
                    text = await response.text()
                    logger.error(f"Review notification failed: {response.status} - {text}")

        except Exception as e:
            logger.error(f"Failed to notify for review: {e}")

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
