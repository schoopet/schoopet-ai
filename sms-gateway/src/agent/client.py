"""Vertex AI Agent Engine client wrapper."""
import logging
import time
from typing import Optional, Union

import vertexai
from google.genai import types

logger = logging.getLogger(__name__)


class _TokenExpiredError(Exception):
    """Internal signal that the Vertex AI access token expired mid-stream."""


class AgentEngineClient:
    """Client for interacting with Vertex AI Agent Engine.

    Wraps the Agent Engine SDK to provide session management and
    message streaming for the SMS gateway.
    """

    def __init__(
        self,
        project_id: str,
        location: str,
        agent_engine_id: str,
        timeout_seconds: int = 30,
    ):
        """Initialize the Agent Engine client.

        Args:
            project_id: Google Cloud project ID.
            location: Google Cloud region (e.g., us-central1).
            agent_engine_id: The Agent Engine resource ID.
            timeout_seconds: Timeout for agent queries (default: 30).
        """
        self._project_id = project_id
        self._location = location
        self._agent_engine_id = agent_engine_id
        self._timeout = timeout_seconds

        self._resource_name = (
            f"projects/{project_id}/locations/{location}"
            f"/reasoningEngines/{agent_engine_id}"
        )
        self._client = None
        self._adk_app = None
        self._init_client()
        logger.info(f"Initialized AgentEngineClient for {self._resource_name}")

    def _init_client(self):
        """(Re)initialize the Vertex AI client and agent engine reference.

        Called at startup and again whenever the token expires (401).
        """
        self._client = vertexai.Client(project=self._project_id, location=self._location)
        self._adk_app = self._client.agent_engines.get(name=self._resource_name)

    async def create_session(self, user_id: str, state: dict | None = None) -> str:
        """Create a new Agent Engine session.

        Args:
            user_id: User identifier (phone number for SMS gateway).
            state: Optional initial session state (e.g., {"channel": "discord"}).

        Returns:
            The session ID string.
        """
        session = await self._adk_app.async_create_session(
            user_id=user_id, state=state
        )
        session_id = session["id"]
        logger.info(f"Created session {session_id} for user {user_id}")
        return session_id

    async def delete_session(self, user_id: str, session_id: str) -> None:
        """Delete an Agent Engine session.

        Best-effort — failures are logged and swallowed so they cannot
        block downstream session creation.
        """
        try:
            await self._adk_app.async_delete_session(
                user_id=user_id, session_id=session_id
            )
            logger.info(f"Deleted session {session_id} for user {user_id}")
        except Exception as e:
            logger.warning(
                f"Failed to delete session {session_id} for user {user_id}: {e}"
            )

    async def send_message(
        self,
        user_id: str,
        session_id: str,
        message: Union[str, types.Content],
    ) -> str:
        """Send a message to the agent and collect the full response.

        Streams the response from the agent and concatenates all text parts
        into a single response string.

        Args:
            user_id: User identifier (phone number).
            session_id: Agent Engine session ID.
            message: The user's message — either a plain string or a
                     types.Content with multimodal parts (text + inline_data).

        Returns:
            The complete agent response text.

        Raises:
            asyncio.TimeoutError: If the agent doesn't respond within timeout.
        """
        logger.info(f"Sending message to agent: user={user_id}, session={session_id}")

        full_response = []
        t_start = time.monotonic()
        ttft_ms: float | None = None

        async def stream_response():
            nonlocal ttft_ms
            async for event in self._adk_app.async_stream_query(
                user_id=user_id,
                session_id=session_id,
                message=message,
            ):
                # Reinitialize client on expired token and propagate so caller retries
                if isinstance(event, dict) and event.get("code") == 401:
                    logger.warning("Access token expired — reinitializing Vertex AI client")
                    self._init_client()
                    raise _TokenExpiredError()
                # Debug: log raw event
                logger.debug(f"Event type: {type(event).__name__}, event: {event}")

                # Handle dict events (from async_stream_query)
                if isinstance(event, dict):
                    # Check for error events from Agent Engine
                    if "code" in event and "message" in event:
                        logger.error(
                            f"Agent Engine error: code={event['code']}, "
                            f"message={event['message']}"
                        )
                        continue

                    content = event.get("content", {})
                    if isinstance(content, dict):
                        parts = content.get("parts", [])
                        for part in parts:
                            if isinstance(part, dict) and "text" in part:
                                if ttft_ms is None:
                                    ttft_ms = (time.monotonic() - t_start) * 1000
                                full_response.append(part["text"])
                    continue

                # Handle object events (fallback)
                if hasattr(event, "content") and event.content:
                    if hasattr(event.content, "parts"):
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                if ttft_ms is None:
                                    ttft_ms = (time.monotonic() - t_start) * 1000
                                full_response.append(part.text)

        # Apply timeout to the streaming operation; retry once on token expiry
        try:
            await asyncio.wait_for(stream_response(), timeout=self._timeout)
        except _TokenExpiredError:
            logger.info("Retrying stream after token refresh")
            full_response.clear()
            ttft_ms = None
            t_start = time.monotonic()
            await asyncio.wait_for(stream_response(), timeout=self._timeout)

        total_ms = (time.monotonic() - t_start) * 1000
        response_text = "".join(full_response)
        logger.info(
            f"Agent response: {len(response_text)} chars "
            f"[ttft={ttft_ms:.0f}ms, total={total_ms:.0f}ms]"
            if ttft_ms is not None
            else f"Agent response: {len(response_text)} chars [no text received, total={total_ms:.0f}ms]"
        )

        return response_text
