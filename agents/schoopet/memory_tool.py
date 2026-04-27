import logging

from google.adk.agents.context import Context
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.tools import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)


def _require_context(tool_context: ToolContext, action: str) -> str | None:
    """Validate tool context for scoped memory writes."""
    if not tool_context or not getattr(tool_context, "user_id", None):
        return (
            f"ERROR: Cannot {action} — no user_id in tool_context. "
            "Memory not saved for security reasons."
        )
    return None


def _normalize_facts(facts: list[str] | str) -> list[str]:
    """Accept either a list or comma-separated string of facts."""
    if isinstance(facts, str):
        return [fact.strip() for fact in facts.split(",") if fact.strip()]
    return [fact.strip() for fact in facts if fact and fact.strip()]


def _build_memory_entry(fact: str) -> MemoryEntry:
    """Create a canonical ADK memory entry from a single fact."""
    return MemoryEntry(
        author="agent",
        content=types.Content(parts=[types.Part(text=fact)]),
    )


async def save_memory(fact: str, tool_context: ToolContext = None) -> str:
    """Save one explicit fact via ADK memory with consolidation enabled."""
    err = _require_context(tool_context, "save memory")
    if err:
        return err

    try:
        await tool_context.add_memory(
            memories=[_build_memory_entry(fact)],
            custom_metadata={"enable_consolidation": True},
        )
        return f"✓ Saved: '{fact}'"
    except Exception as e:
        return f"Error saving memory: {str(e)}"


async def save_multiple_memories(
    facts: list[str] | str,
    tool_context: ToolContext = None,
) -> str:
    """Save multiple explicit facts via ADK memory with consolidation enabled."""
    err = _require_context(tool_context, "save memories")
    if err:
        return err

    normalized_facts = _normalize_facts(facts)
    try:
        await tool_context.add_memory(
            memories=[_build_memory_entry(fact) for fact in normalized_facts],
            custom_metadata={"enable_consolidation": True},
        )
        return f"✓ Saved {len(normalized_facts)} memories successfully."
    except Exception as e:
        return f"Error saving memories: {str(e)}"


async def save_session_to_memory(callback_context: Context) -> None:
    """Best-effort canonical ADK session ingestion after each agent turn."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        logger.warning("Failed to add session to memory: %s", e)
