"""Vertex AI Agent Engine client wrapper."""
import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Any, Union

import vertexai
from google.adk.events import Event
from google.genai import types

logger = logging.getLogger(__name__)


class _TokenExpiredError(Exception):
    """Internal signal that the Vertex AI access token expired mid-stream."""


@dataclass(frozen=True)
class AdkConfirmationRequest:
    """Native ADK confirmation request extracted from an event stream."""

    function_call_id: str
    original_function_call: dict[str, Any]
    tool_confirmation: dict[str, Any]

    @property
    def tool_name(self) -> str:
        return str(self.original_function_call.get("name") or "unknown_tool")

    @property
    def tool_args(self) -> dict[str, Any]:
        args = self.original_function_call.get("args")
        return args if isinstance(args, dict) else {}

    @property
    def original_function_call_id(self) -> str:
        return str(self.original_function_call.get("id") or "")

    @property
    def hint(self) -> str:
        return str(self.tool_confirmation.get("hint") or "")

    @property
    def payload(self) -> Any:
        return self.tool_confirmation.get("payload")

    def to_firestore(self) -> dict[str, Any]:
        return {
            "function_call_id": self.function_call_id,
            "original_function_call": self.original_function_call,
            "tool_confirmation": self.tool_confirmation,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "original_function_call_id": self.original_function_call_id,
            "hint": self.hint,
            "payload": self.payload,
        }


def _log_tool_activity(event: Event, user_id: str) -> None:
    """Log function calls and responses from an ADK event."""
    uid = f"{user_id[:4]}****" if len(user_id) > 4 else user_id

    for fc in event.get_function_calls() or []:
        args = fc.args or {}
        # Truncate large args (e.g. email bodies) to keep logs readable
        summarized = {k: (str(v)[:120] if isinstance(v, str) else v) for k, v in args.items()}
        logger.info(f"Tool call [{uid}]: {fc.name}({summarized})")

    for fr in event.get_function_responses() or []:
        response = fr.response or {}
        output = response.get("output") or response.get("result") or response
        output_str = str(output)
        if len(output_str) > 200:
            output_str = output_str[:200] + "…"
        logger.info(f"Tool response [{uid}]: {fr.name} → {output_str}")


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
        events = await self.send_message_events(user_id, session_id, message)
        return self.extract_text(events)

    async def send_message_events(
        self,
        user_id: str,
        session_id: str,
        message: Union[str, types.Content],
    ) -> list[Event]:
        """Send a message to the agent and return native ADK events."""
        logger.info(f"Sending message to agent: user={user_id}, session={session_id}")

        events: list[Event] = []
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
                logger.debug(f"Event type: {type(event).__name__}, event: {event}")

                if isinstance(event, dict):
                    if "code" in event and "message" in event:
                        logger.error(
                            f"Agent Engine error: code={event['code']}, "
                            f"message={event['message']}"
                        )
                        continue
                    parsed_event = self._coerce_event(event)
                    events.append(parsed_event)
                    if ttft_ms is None and self.extract_text([parsed_event]):
                        ttft_ms = (time.monotonic() - t_start) * 1000
                    _log_tool_activity(parsed_event, user_id)
                    continue

                parsed_event = event if isinstance(event, Event) else Event.model_validate(event)
                events.append(parsed_event)
                if ttft_ms is None and self.extract_text([parsed_event]):
                    ttft_ms = (time.monotonic() - t_start) * 1000
                _log_tool_activity(parsed_event, user_id)

        # Apply timeout to the streaming operation; retry once on token expiry
        try:
            await asyncio.wait_for(stream_response(), timeout=self._timeout)
        except _TokenExpiredError:
            logger.info("Retrying stream after token refresh")
            events.clear()
            ttft_ms = None
            t_start = time.monotonic()
            await asyncio.wait_for(stream_response(), timeout=self._timeout)

        total_ms = (time.monotonic() - t_start) * 1000
        response_text = self.extract_text(events)
        logger.info(
            f"Agent response: {len(response_text)} chars "
            f"[ttft={ttft_ms:.0f}ms, total={total_ms:.0f}ms]"
            if ttft_ms is not None
            else f"Agent response: {len(response_text)} chars [no text received, total={total_ms:.0f}ms]"
        )

        return events

    async def send_confirmation_response(
        self,
        user_id: str,
        session_id: str,
        confirmation_function_call_id: str,
        confirmed: bool,
        reason: str | None = None,
    ) -> list[Event]:
        """Resolve an ADK confirmation request with a native function response."""
        response_payload: dict = {"confirmed": confirmed}
        if reason:
            response_payload["reason"] = reason
        content = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="adk_request_confirmation",
                        id=confirmation_function_call_id,
                        response=response_payload,
                    )
                )
            ],
        )
        return await self.send_message_events(user_id, session_id, content)

    @staticmethod
    def extract_text(events: list[Event]) -> str:
        """Extract concatenated text parts from ADK events."""
        chunks: list[str] = []
        for event in events:
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def extract_confirmation_requests(events: list[Event]) -> list[AdkConfirmationRequest]:
        """Extract native ADK confirmation function calls from events."""
        confirmations: list[AdkConfirmationRequest] = []
        for event in events:
            for function_call in event.get_function_calls() or []:
                if function_call.name != "adk_request_confirmation":
                    continue
                args = function_call.args or {}
                original = args.get("originalFunctionCall") or args.get("original_function_call") or {}
                tool_confirmation = args.get("toolConfirmation") or args.get("tool_confirmation") or {}
                confirmations.append(
                    AdkConfirmationRequest(
                        function_call_id=str(function_call.id or ""),
                        original_function_call=original if isinstance(original, dict) else {},
                        tool_confirmation=(
                            tool_confirmation if isinstance(tool_confirmation, dict) else {}
                        ),
                    )
                )
        return confirmations

    @staticmethod
    def _coerce_event(event: dict) -> Event:
        """Validate a stream dict into an ADK Event, with a legacy text fallback."""
        try:
            return Event.model_validate(event)
        except Exception:
            content = event.get("content")
            if isinstance(content, dict):
                return Event(
                    author=str(event.get("author") or "agent"),
                    content=types.Content.model_validate(content),
                )
            raise
