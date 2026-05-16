"""ADK plugin that short-circuits the LLM for deterministic admin commands.

Gateway sends messages like ``__ADMIN:setup_gmail_watch__`` to trigger a tool
directly without consuming LLM tokens.  The plugin intercepts these in
``before_model_callback``, executes the named tool using the same Context
object (ToolContext = CallbackContext = Context), and returns a suppressed
LlmResponse so nothing is forwarded to the user.
"""
import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

logger = logging.getLogger(__name__)

_ADMIN_PREFIX = "__ADMIN:"
_ADMIN_SUFFIX = "__"


class AdminCommandPlugin(BasePlugin):
    """Executes deterministic admin commands directly, bypassing the LLM."""

    def __init__(self) -> None:
        super().__init__(name="admin_command_plugin")

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        user_content = callback_context.user_content
        if not user_content or not user_content.parts:
            return None
        text = (user_content.parts[0].text or "").strip()
        if not text.startswith(_ADMIN_PREFIX):
            return None

        inner = text[len(_ADMIN_PREFIX):]
        end = inner.find(_ADMIN_SUFFIX)
        command = inner[:end] if end >= 0 else inner.rstrip("_")

        tool = llm_request.tools_dict.get(command)
        if not tool:
            logger.warning("AdminCommandPlugin: unknown command %r — ignoring", command)
        else:
            uid = (callback_context.user_id or "?")[:4]
            logger.info("AdminCommandPlugin: executing %r for user %s****", command, uid)
            try:
                result = await tool.run_async(args={}, tool_context=callback_context)
                logger.info("AdminCommandPlugin: %r completed: %s", command, result)
            except Exception as exc:
                logger.error("AdminCommandPlugin: %r failed: %s", command, exc)

        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="<SUPPRESS RESPONSE>")],
            )
        )
