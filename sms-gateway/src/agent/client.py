"""Vertex AI Agent Engine client wrapper."""
import asyncio
from dataclasses import dataclass, field
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
class AdkCredentialRequest:
    """An adk_request_credential function call extracted from an event stream."""

    function_call_id: str
    original_function_call_id: str
    auth_uri: str
    nonce: str
    auth_config_dict: dict

    def to_firestore(self) -> dict[str, Any]:
        return {
            "function_call_id": self.function_call_id,
            "original_function_call_id": self.original_function_call_id,
            "auth_uri": self.auth_uri,
            "nonce": self.nonce,
            "auth_config_dict": self.auth_config_dict,
        }


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
        # Empty result on a workspace tool almost always means the IAM connector
        # credential was auto-resolved but the underlying API call still failed
        # (e.g. token not yet propagated, wrong scopes, or connector state mismatch).
        if "result" in response and response["result"] == "":
            logger.warning(
                f"Tool response [{uid}]: {fr.name} → EMPTY RESULT "
                f"(credential may not be providing a valid token)"
            )
        else:
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
            user_id: User identifier.
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
            user_id: User identifier.
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
    def extract_gcp_auto_credential_requests(events: list[Event]) -> list[tuple[str, dict]]:
        """Find gcpAuthProviderScheme adk_request_credential calls without an auth URI.

        These occur when the IAM connector credential is already stored in the backend
        (user already consented) but ADK's session state still has a pending credential
        request. The gateway should auto-respond so ADK can fetch the stored token
        without prompting the user again.

        Returns list of (function_call_id, auth_config_dict) tuples.
        """
        results = []
        for event in events:
            for fc in event.get_function_calls() or []:
                if fc.name != "adk_request_credential":
                    continue
                args = fc.args or {}
                auth_config = args.get("authConfig") or args.get("auth_config") or {}
                if not isinstance(auth_config, dict):
                    continue
                scheme_type = (auth_config.get("authScheme") or {}).get("type", "")
                if scheme_type != "gcpAuthProviderScheme":
                    continue
                exchanged = auth_config.get("exchangedAuthCredential") or {}
                oauth2 = exchanged.get("oauth2") or {}
                if oauth2.get("auth_uri") or oauth2.get("authUri"):
                    continue  # has auth URI — needs user action, handled by extract_credential_requests
                credential_key = auth_config.get("credentialKey", "unknown")
                logger.info(
                    f"[auto-cred] Stored IAM credential request: "
                    f"fc_id={fc.id!r} credentialKey={credential_key!r}"
                )
                results.append((str(fc.id or ""), auth_config))
        if results:
            logger.info(f"[auto-cred] {len(results)} auto-resolvable credential request(s) found")
        return results

    @staticmethod
    def extract_credential_requests(events: list[Event]) -> list[AdkCredentialRequest]:
        """Extract adk_request_credential function calls from events."""
        requests: list[AdkCredentialRequest] = []
        for event in events:
            for function_call in event.get_function_calls() or []:
                if function_call.name != "adk_request_credential":
                    continue
                args = function_call.args or {}
                fc_id = str(function_call.id or "")
                original_fc_id = str(args.get("functionCallId") or args.get("function_call_id") or "")
                auth_config = args.get("authConfig") or args.get("auth_config") or {}
                if isinstance(auth_config, dict):
                    auth_config_dict = auth_config
                else:
                    try:
                        auth_config_dict = dict(auth_config)
                    except Exception:
                        auth_config_dict = {}
                scheme_type = (auth_config_dict.get("authScheme") or {}).get("type", "")
                credential_key = auth_config_dict.get("credentialKey", "unknown")
                exchanged = (auth_config_dict.get("exchangedAuthCredential")
                             or auth_config_dict.get("exchanged_auth_credential") or {})
                oauth2 = exchanged.get("oauth2") or {}
                auth_uri = str(oauth2.get("auth_uri") or oauth2.get("authUri") or "")
                nonce = str(oauth2.get("nonce") or "")
                logger.info(
                    f"[cred-extract] adk_request_credential: fc_id={fc_id!r} "
                    f"scheme={scheme_type!r} credentialKey={credential_key!r} "
                    f"has_auth_uri={bool(auth_uri)}"
                )
                if not auth_uri:
                    continue
                logger.warning(
                    f"[cred-extract] Interactive auth URI required: fc_id={fc_id!r} "
                    f"credentialKey={credential_key!r} nonce={nonce!r} "
                    f"auth_uri={auth_uri[:80]}..."
                )
                requests.append(
                    AdkCredentialRequest(
                        function_call_id=fc_id,
                        original_function_call_id=original_fc_id,
                        auth_uri=auth_uri,
                        nonce=nonce,
                        auth_config_dict=auth_config_dict,
                    )
                )
        return requests

    async def send_credential_response(
        self,
        user_id: str,
        session_id: str,
        credential_function_call_id: str,
        auth_config_dict: dict,
    ) -> list[Event]:
        """Resume an agent session after IAM connector consent is complete."""
        uid = f"{user_id[:4]}****" if len(user_id) > 4 else user_id
        credential_key = auth_config_dict.get("credentialKey", "unknown")
        logger.info(
            f"Sending credential response: user={uid} "
            f"fc_id={credential_function_call_id!r} credentialKey={credential_key!r}"
        )
        content = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="adk_request_credential",
                        id=credential_function_call_id,
                        response=auth_config_dict,
                    )
                )
            ],
        )
        return await self.send_message_events(user_id, session_id, content)

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
