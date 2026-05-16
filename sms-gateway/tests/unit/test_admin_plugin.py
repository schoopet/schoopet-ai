"""Unit tests for the ADK AdminCommandPlugin.

The plugin short-circuits before_model_callback for messages matching
``__ADMIN:<tool_name>__``, executes the named tool directly, and returns a
suppressed LlmResponse — bypassing the LLM entirely.
"""
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

# Admin plugin lives in the agents package; add it to the path so gateway
# tests can import it without any heavyweight agent dependencies.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent.parent / "agents"))
from schoopet.admin_plugin import AdminCommandPlugin  # noqa: E402


def _make_context(text: str, user_id: str = "user-123"):
    return SimpleNamespace(
        user_content=types.Content(
            role="user", parts=[types.Part(text=text)]
        ),
        user_id=user_id,
    )


def _make_request(tools: dict | None = None):
    return SimpleNamespace(tools_dict=tools or {})


@pytest.fixture
def plugin():
    return AdminCommandPlugin()


# ─────────────────────────── passthrough cases ───────────────────────────────

@pytest.mark.asyncio
async def test_non_admin_message_returns_none(plugin):
    ctx = _make_context("Hey, what's on my calendar?")
    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=_make_request()
    )
    assert result is None


@pytest.mark.asyncio
async def test_empty_content_returns_none(plugin):
    ctx = SimpleNamespace(user_content=None, user_id="user-123")
    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=_make_request()
    )
    assert result is None


@pytest.mark.asyncio
async def test_empty_parts_returns_none(plugin):
    ctx = SimpleNamespace(
        user_content=types.Content(role="user", parts=[]),
        user_id="user-123",
    )
    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=_make_request()
    )
    assert result is None


# ─────────────────────────── admin command cases ─────────────────────────────

@pytest.mark.asyncio
async def test_known_command_executes_tool_and_suppresses(plugin):
    mock_tool = AsyncMock()
    mock_tool.run_async = AsyncMock(return_value="Gmail watch registered.")

    ctx = _make_context("__ADMIN:setup_gmail_watch__")
    req = _make_request({"setup_gmail_watch": mock_tool})

    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=req
    )

    mock_tool.run_async.assert_awaited_once_with(args={}, tool_context=ctx)
    assert isinstance(result, LlmResponse)
    assert result.content.parts[0].text == "<SUPPRESS RESPONSE>"


@pytest.mark.asyncio
async def test_unknown_command_suppresses_without_tool_call(plugin):
    mock_tool = AsyncMock()
    mock_tool.run_async = AsyncMock()

    ctx = _make_context("__ADMIN:nonexistent_tool__")
    req = _make_request({"setup_gmail_watch": mock_tool})

    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=req
    )

    mock_tool.run_async.assert_not_awaited()
    assert isinstance(result, LlmResponse)
    assert result.content.parts[0].text == "<SUPPRESS RESPONSE>"


@pytest.mark.asyncio
async def test_tool_exception_still_returns_suppressed(plugin):
    mock_tool = AsyncMock()
    mock_tool.run_async = AsyncMock(side_effect=RuntimeError("watch API error"))

    ctx = _make_context("__ADMIN:setup_gmail_watch__")
    req = _make_request({"setup_gmail_watch": mock_tool})

    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=req
    )

    # Even on failure the plugin must not let an exception surface — gateway
    # treats the suppressed response as a clean no-op.
    assert isinstance(result, LlmResponse)
    assert result.content.parts[0].text == "<SUPPRESS RESPONSE>"


@pytest.mark.asyncio
async def test_tool_context_passed_is_the_callback_context(plugin):
    """Confirms the same Context object is forwarded (ToolContext = Context alias)."""
    received: list = []

    async def _capture(**kwargs):
        received.append(kwargs.get("tool_context"))
        return "ok"

    mock_tool = AsyncMock()
    mock_tool.run_async = AsyncMock(side_effect=_capture)

    ctx = _make_context("__ADMIN:setup_gmail_watch__")
    req = _make_request({"setup_gmail_watch": mock_tool})

    await plugin.before_model_callback(callback_context=ctx, llm_request=req)

    assert received == [ctx]


@pytest.mark.asyncio
async def test_whitespace_around_command_is_handled(plugin):
    mock_tool = AsyncMock()
    mock_tool.run_async = AsyncMock(return_value="ok")

    # strip() in plugin normalises leading/trailing whitespace
    ctx = _make_context("  __ADMIN:setup_gmail_watch__  ")
    req = _make_request({"setup_gmail_watch": mock_tool})

    result = await plugin.before_model_callback(
        callback_context=ctx, llm_request=req
    )

    mock_tool.run_async.assert_awaited_once()
    assert result is not None
