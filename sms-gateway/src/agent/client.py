"""Vertex AI Agent Engine client wrapper."""
import asyncio
import logging
from typing import Optional

import vertexai

logger = logging.getLogger(__name__)


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

        # Initialize Vertex AI client
        self._client = vertexai.Client(project=project_id, location=location)

        # Get agent engine reference
        resource_name = (
            f"projects/{project_id}/locations/{location}"
            f"/reasoningEngines/{agent_engine_id}"
        )
        self._adk_app = self._client.agent_engines.get(name=resource_name)

        logger.info(f"Initialized AgentEngineClient for {resource_name}")

    async def create_session(self, user_id: str) -> str:
        """Create a new Agent Engine session.

        Args:
            user_id: User identifier (phone number for SMS gateway).

        Returns:
            The session ID string.
        """
        session = await self._adk_app.async_create_session(user_id=user_id)
        session_id = session["id"]
        logger.info(f"Created session {session_id} for user {user_id}")
        return session_id

    async def send_message(
        self,
        user_id: str,
        session_id: str,
        message: str,
    ) -> str:
        """Send a message to the agent and collect the full response.

        Streams the response from the agent and concatenates all text parts
        into a single response string.

        Args:
            user_id: User identifier (phone number).
            session_id: Agent Engine session ID.
            message: The user's message text.

        Returns:
            The complete agent response text.

        Raises:
            asyncio.TimeoutError: If the agent doesn't respond within timeout.
        """
        logger.info(f"Sending message to agent: user={user_id}, session={session_id}")

        full_response = []

        async def stream_response():
            async for event in self._adk_app.async_stream_query(
                user_id=user_id,
                session_id=session_id,
                message=message,
            ):
                # Debug: log raw event
                logger.debug(f"Event type: {type(event).__name__}, event: {event}")

                # Handle dict events (from async_stream_query)
                if isinstance(event, dict):
                    content = event.get("content", {})
                    if isinstance(content, dict):
                        parts = content.get("parts", [])
                        for part in parts:
                            if isinstance(part, dict) and "text" in part:
                                full_response.append(part["text"])
                    continue

                # Handle object events (fallback)
                if hasattr(event, "content") and event.content:
                    if hasattr(event.content, "parts"):
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                full_response.append(part.text)

        # Apply timeout to the streaming operation
        await asyncio.wait_for(stream_response(), timeout=self._timeout)

        response_text = "".join(full_response)
        logger.info(f"Agent response length: {len(response_text)} chars")

        return response_text
