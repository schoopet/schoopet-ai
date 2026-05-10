"""Agent callbacks: before_model and on_tool_error.

before_model_modifier — injects artifact inline_data into the model request.
When a tool returns a dict containing 'tool_response_artifact_id', this callback
loads the corresponding artifact from the session registry and appends it as an
inline_data Part directly after the tool's FunctionResponse Part.
Gemini then sees the artifact bytes natively in its context.

on_tool_error — returns a graceful error dict instead of raising when ADK
cannot find a tool the model called (hallucinated tool names, namespace
prefixes, etc.). The model receives the error as a function response and can
recover by calling the correct tool.
"""
import logging

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

logger = logging.getLogger(__name__)


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
