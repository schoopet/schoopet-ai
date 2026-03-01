"""before_model_callback that injects artifact inline_data into the model request.

When a tool returns a dict containing 'tool_response_artifact_id', this callback
loads the corresponding artifact from the session registry and appends it as an
inline_data Part directly after the tool's FunctionResponse Part.
Gemini then sees the artifact bytes natively in its context.
"""
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types


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
