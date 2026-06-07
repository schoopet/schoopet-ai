"""Agent callbacks: before_model, after_model, and on_tool_error.

before_model_modifier — injects artifact inline_data into the model request.
When a tool returns a dict containing 'tool_response_artifact_id', this callback
loads the corresponding artifact from the session registry and appends it as an
inline_data Part directly after the tool's FunctionResponse Part.
Gemini then sees the artifact bytes natively in its context.

after_model_token_counter — writes a structured log entry per Gemini call to
the agent_token_usage log, exported to BigQuery via a log sink. Includes
user_id, source (email/async_task/conversation), model_version, and token counts.

on_tool_error — returns a graceful error dict instead of raising when ADK
cannot find a tool the model called (hallucinated tool names, namespace
prefixes, etc.). The model receives the error as a function response and can
recover by calling the correct tool.
"""
import asyncio
import logging
import os

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

_gcp_log_client = None
_TOKEN_USAGE_LOG = "agent_token_usage"


def _get_gcp_logger(project_id: str):
    global _gcp_log_client
    if _gcp_log_client is None:
        from google.cloud import logging as gcloud_logging
        _gcp_log_client = gcloud_logging.Client(project=project_id)
    return _gcp_log_client.logger(_TOKEN_USAGE_LOG)


def _write_token_log(
    project_id: str,
    user_id: str,
    source: str,
    input_tokens: int,
    output_tokens: int,
    model_version: str,
) -> None:
    try:
        gcp_logger = _get_gcp_logger(project_id)
        gcp_logger.log_struct({
            "user_id": user_id,
            "source": source,
            "model_version": model_version,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        })
    except Exception:
        logger.warning("Failed to write token usage log entry", exc_info=True)


async def _emit_token_log(
    project_id: str,
    user_id: str,
    source: str,
    input_tokens: int,
    output_tokens: int,
    model_version: str,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _write_token_log,
        project_id, user_id, source, input_tokens, output_tokens, model_version,
    )


async def after_model_token_counter(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Emit token usage metrics to Cloud Monitoring and Cloud Logging, tagged by source and user."""
    usage = llm_response.usage_metadata
    if not usage:
        return None

    input_tokens = usage.prompt_token_count or 0
    output_tokens = usage.candidates_token_count or 0
    if input_tokens == 0 and output_tokens == 0:
        return None

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        return None

    channel = callback_context.state.get("channel", "unknown")
    task_type = callback_context.state.get("task_type", "")
    if channel == "email":
        source = "email"
    elif task_type == "async_task":
        source = "async_task"
    else:
        source = "conversation"

    user_id = callback_context._invocation_context.user_id or "unknown"
    model_version = llm_response.model_version or "unknown"

    asyncio.create_task(_emit_token_log(
        project_id, user_id, source, input_tokens, output_tokens, model_version,
    ))
    return None


async def before_model_modifier(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Inject artifact bytes for any function response that carries tool_response_artifact_id."""
    for content in llm_request.contents:
        if not content.parts:
            continue
        modified_parts = []
        for part in content.parts:
            if (
                part.function_response
                and part.function_response.response
                and part.function_response.response.get("tool_response_artifact_id")
            ):
                artifact_id = part.function_response.response["tool_response_artifact_id"]
                artifact = await callback_context.load_artifact(filename=artifact_id)
                modified_parts.append(part)  # original function response
                if artifact is not None:
                    modified_parts.append(
                        types.Part(text=f"[Artifact content for: {artifact_id}]")
                    )
                    modified_parts.append(artifact)
            else:
                modified_parts.append(part)
        content.parts = modified_parts
    return None  # returning None lets the model call proceed normally


async def on_tool_error(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    error: Exception,
) -> dict | None:
    """Return a graceful error response instead of raising on unknown tool calls.

    ADK calls this when a tool lookup or execution fails. Returning a non-None
    dict sends it back to the model as the function response so it can recover
    (e.g. retry with the correct tool name) instead of crashing the session.
    """
    logger.warning(
        "Tool error for '%s' (args=%s): %s",
        getattr(tool, "name", "unknown"),
        list(args.keys()),
        error,
    )
    return {
        "error": (
            f"Tool '{getattr(tool, 'name', 'unknown')}' could not be executed: {error}. "
            "Please check the exact tool name and retry with the correct one."
        )
    }
