"""Task Worker - Executes async tasks and notifies for review.

This module handles:
1. Loading task definitions from Firestore
2. Executing tasks using specialized async agents
3. Handling different memory isolation levels
4. Notifying SMS Gateway for root agent review
5. Processing revision requests
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class TaskWorker:
    """Executes async tasks spawned by the root agent."""

    def __init__(self):
        """Initialize the task worker - uses lazy initialization."""
        self._firestore_client = None
        self._vertex_client = None
        self._http_client = None
        self._initialized = False

        # Configuration
        self._project_id = None
        self._location = None
        self._agent_engine_id = None
        self._sms_gateway_url = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration and clients."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self._agent_engine_id = os.getenv("AGENT_ENGINE_ID")
        self._sms_gateway_url = os.getenv("SMS_GATEWAY_URL")

        self._initialized = True

        if not self._project_id or not self._agent_engine_id:
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

        # Get task from Firestore
        task = await self._get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        # Check if task can be executed
        status = task.get("status")
        if status not in ["pending", "scheduled", "revision_requested"]:
            logger.info(f"Task {task_id} already processed: {status}")
            return {"success": True, "message": f"Task already in status: {status}"}

        # Update status to running
        await self._update_task_status(task_id, "running")

        try:
            # Execute based on task type and memory isolation
            memory_isolation = task.get("memory_isolation", "shared")

            if status == "revision_requested":
                # Continue with revision
                result, memories = await self._execute_revision(task)
            elif memory_isolation == "isolated":
                result, memories = await self._execute_isolated(task)
            elif memory_isolation == "readonly":
                result, memories = await self._execute_readonly(task)
            else:  # shared
                result, memories = await self._execute_shared(task)

            # Update task with results
            await self._update_task_result(
                task_id=task_id,
                result=result,
                memories=memories,
                status="awaiting_review"
            )

            # Notify SMS Gateway for root agent review
            await self._notify_for_review(
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
            await self._notify_for_review(
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
        memories: list,
        status: str
    ):
        """Update task with execution result."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return

        firestore_client.collection("async_tasks").document(task_id).update({
            "status": status,
            "result": result,
            "result_memories": memories,
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

    async def _execute_shared(self, task: Dict[str, Any]) -> Tuple[str, list]:
        """Execute task with full access to user's Memory Bank.

        Creates a session with the user's memory context.
        """
        from .async_agent import create_async_agent

        agent = create_async_agent(
            task_type=task["task_type"],
            project=self._project_id,
            location=self._location,
        )

        # Build prompt with task context
        prompt = self._build_task_prompt(task)

        # Execute in shared mode (agent has access to memory)
        result = await agent.execute(prompt, task.get("context", {}))

        return result, []  # No memories to sync, already in shared bank

    async def _execute_isolated(self, task: Dict[str, Any]) -> Tuple[str, list]:
        """Execute task with isolated memory.

        Collects memories during execution to sync on completion.
        """
        from .async_agent import create_async_agent

        agent = create_async_agent(
            task_type=task["task_type"],
            project=self._project_id,
            location=self._location,
            collect_memories=True,
        )

        prompt = self._build_task_prompt(task)
        result, memories = await agent.execute_with_memory_collection(
            prompt, task.get("context", {})
        )

        return result, memories

    async def _execute_readonly(self, task: Dict[str, Any]) -> Tuple[str, list]:
        """Execute task with read-only memory access.

        Retrieves relevant memories as context but doesn't write.
        """
        from .async_agent import create_async_agent

        # Retrieve relevant memories for context
        memories_context = await self._retrieve_user_memories(
            user_id=task["user_id"],
            query=task["instruction"]
        )

        agent = create_async_agent(
            task_type=task["task_type"],
            project=self._project_id,
            location=self._location,
            preloaded_memories=memories_context,
        )

        prompt = self._build_task_prompt(task)
        result = await agent.execute(prompt, task.get("context", {}))

        return result, []

    async def _execute_revision(self, task: Dict[str, Any]) -> Tuple[str, list]:
        """Execute task revision based on supervisor feedback.

        Uses the existing async session if available, with feedback context.
        """
        from .async_agent import create_async_agent

        agent = create_async_agent(
            task_type=task["task_type"],
            project=self._project_id,
            location=self._location,
        )

        # Build revision prompt with feedback
        prompt = self._build_revision_prompt(task)
        result = await agent.execute(prompt, task.get("context", {}))

        return result, []

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
        parts.append("Provide a concise result that can be sent to the user via SMS.")
        parts.append("Keep the response under 1000 characters if possible.")

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
            "Keep the response under 1000 characters if possible.",
        ]

        return "\n".join(parts)

    async def _retrieve_user_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = 10
    ) -> list:
        """Retrieve relevant memories for the user.

        Used for readonly execution mode.
        """
        try:
            vertex_client = self._get_vertex_client()
            if not vertex_client:
                return []

            agent_engine_name = (
                f"projects/{self._project_id}/locations/{self._location}"
                f"/reasoningEngines/{self._agent_engine_id}"
            )

            # Query memories
            response = vertex_client.agent_engines.memories.retrieve(
                name=agent_engine_name,
                scope={"user_id": user_id},
                similarity_search_params={
                    "query": query,
                    "top_k": top_k
                }
            )

            memories = []
            for memory in response.memories:
                if hasattr(memory, "fact"):
                    memories.append(memory.fact)

            return memories

        except Exception as e:
            logger.warning(f"Failed to retrieve memories: {e}")
            return []

    async def _notify_for_review(
        self,
        task_id: str,
        user_id: str,
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
