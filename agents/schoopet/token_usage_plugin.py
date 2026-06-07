"""Plugin that logs token usage per Gemini call to Cloud Logging -> BigQuery.

Covers the root agent and all sub-agents automatically (plugin scope is global
across the entire runner, unlike agent-level callbacks).

Each log entry includes:
  user_id, agent_name, source (email/async_task/conversation),
  model_version, input_tokens, output_tokens, total_tokens

Logs are written to the agent_token_usage log and exported to BigQuery via
a Cloud Logging sink configured in terraform/agent_metrics.tf.
"""
import asyncio
import logging
import os

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

_TOKEN_USAGE_LOG = "agent_token_usage"

_gcp_log_client = None


def _get_gcp_logger(project_id: str):
    global _gcp_log_client
    if _gcp_log_client is None:
        from google.cloud import logging as gcloud_logging
        _gcp_log_client = gcloud_logging.Client(project=project_id)
    return _gcp_log_client.logger(_TOKEN_USAGE_LOG)


def _write_token_log(
    project_id: str,
    user_id: str,
    agent_name: str,
    source: str,
    input_tokens: int,
    output_tokens: int,
    model_version: str,
) -> None:
    try:
        gcp_logger = _get_gcp_logger(project_id)
        gcp_logger.log_struct({
            "user_id": user_id,
            "agent_name": agent_name,
            "source": source,
            "model_version": model_version,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        })
    except Exception:
        logger.warning("Failed to write token usage log entry", exc_info=True)


class TokenUsagePlugin(BasePlugin):
    """Logs token usage for every Gemini call across all agents."""

    def __init__(self) -> None:
        super().__init__(name="token_usage_plugin")

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
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
        agent = callback_context._invocation_context.agent
        agent_name = agent.name if agent else "unknown"
        model_version = llm_response.model_version or "unknown"

        loop = asyncio.get_event_loop()
        asyncio.create_task(
            loop.run_in_executor(
                None, _write_token_log,
                project_id, user_id, agent_name, source,
                input_tokens, output_tokens, model_version,
            )
        )
        return None
