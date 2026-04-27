"""Unit tests for memory tool functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.schoopet.memory_tool import (
    save_memory,
    save_multiple_memories,
    save_session_to_memory,
)
from google.adk.agents.context import Context
from google.adk.tools import ToolContext


USER_ID = "+14155551234"
MEMORY_FACT = "Test fact"


@pytest.fixture
def tool_context():
    context = MagicMock(spec=ToolContext)
    context.user_id = USER_ID
    context.add_memory = AsyncMock()
    return context


class TestMemoryTools:
    @pytest.mark.asyncio
    async def test_save_memory_success(self, tool_context):
        result = await save_memory(MEMORY_FACT, tool_context)

        assert "Saved" in result
        tool_context.add_memory.assert_awaited_once()
        call = tool_context.add_memory.await_args.kwargs
        assert call["custom_metadata"] == {"enable_consolidation": True}
        assert len(call["memories"]) == 1
        assert call["memories"][0].author == "agent"
        assert call["memories"][0].content.parts[0].text == MEMORY_FACT

    @pytest.mark.asyncio
    async def test_save_memory_no_context(self):
        result = await save_memory(MEMORY_FACT, None)

        assert "ERROR: Cannot save memory" in result

    @pytest.mark.asyncio
    async def test_save_memory_no_user_id(self):
        context = MagicMock(spec=ToolContext)
        context.user_id = None
        context.add_memory = AsyncMock()

        result = await save_memory(MEMORY_FACT, context)

        assert "ERROR: Cannot save memory" in result
        context.add_memory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_multiple_memories_list(self, tool_context):
        result = await save_multiple_memories(["Fact 1", "Fact 2"], tool_context)

        assert "Saved 2 memories" in result
        call = tool_context.add_memory.await_args.kwargs
        assert [m.content.parts[0].text for m in call["memories"]] == ["Fact 1", "Fact 2"]
        assert call["custom_metadata"] == {"enable_consolidation": True}

    @pytest.mark.asyncio
    async def test_save_multiple_memories_string(self, tool_context):
        result = await save_multiple_memories("Fact 1, Fact 2 ", tool_context)

        assert "Saved 2 memories" in result
        call = tool_context.add_memory.await_args.kwargs
        assert [m.content.parts[0].text for m in call["memories"]] == ["Fact 1", "Fact 2"]

    @pytest.mark.asyncio
    async def test_save_multiple_memories_ignores_blank_items(self, tool_context):
        result = await save_multiple_memories(["Fact 1", " ", ""], tool_context)

        assert "Saved 1 memories" in result
        call = tool_context.add_memory.await_args.kwargs
        assert [m.content.parts[0].text for m in call["memories"]] == ["Fact 1"]

    @pytest.mark.asyncio
    async def test_save_memory_error(self, tool_context):
        tool_context.add_memory.side_effect = Exception("boom")

        result = await save_memory(MEMORY_FACT, tool_context)

        assert result == "Error saving memory: boom"

    @pytest.mark.asyncio
    async def test_save_session_to_memory(self):
        context = MagicMock(spec=Context)
        context.add_session_to_memory = AsyncMock()

        await save_session_to_memory(context)

        context.add_session_to_memory.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_session_to_memory_swallows_errors(self):
        context = MagicMock(spec=Context)
        context.add_session_to_memory = AsyncMock(side_effect=Exception("boom"))

        await save_session_to_memory(context)
