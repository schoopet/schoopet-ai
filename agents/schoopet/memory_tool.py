import os
from typing import Optional, Dict, Any
from vertexai import Client
from google.adk.tools import ToolContext

import json

class MemoryTool:
    """Tool for explicitly saving facts to Vertex AI Memory Bank."""

    def __init__(self):
        """Initialize the Memory Tool with Vertex AI client."""
        self._client = None
        self.project_id = None
        self.location = None
        self.agent_engine_id = None
        self.agent_engine_name = None

    def _require_scope(self, tool_context: ToolContext, action: str):
        """Return (scope_dict, None) or (None, error_message).

        Validates client initialization and user_id presence. All memory
        operations require a user_id for proper scoping and security.
        """
        if not self.client:
            return None, (
                "Error: Memory tool not initialized. "
                "Check environment variables: GOOGLE_CLOUD_PROJECT, "
                "GOOGLE_CLOUD_LOCATION, and GOOGLE_CLOUD_AGENT_ENGINE_ID."
            )
        if not tool_context or not getattr(tool_context, "user_id", None):
            error_msg = f"ERROR: Cannot {action} — no user_id in tool_context. Memory not saved for security reasons."
            print(error_msg)
            return None, error_msg
        return {"user_id": tool_context.user_id}, None

    @property
    def client(self):
        """Lazy initialization of Vertex AI client for pickling support."""
        if self._client is None:
            # Read environment variables fresh on first access
            self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
            self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            self.agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")

            if all([self.project_id, self.location, self.agent_engine_id]):
                self.agent_engine_name = f"projects/{self.project_id}/locations/{self.location}/reasoningEngines/{self.agent_engine_id}"
                self._client = Client(project=self.project_id, location=self.location)
            else:
                print("Warning: Memory tool requires GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, and GOOGLE_CLOUD_AGENT_ENGINE_ID environment variables.")
                self.agent_engine_name = None

        return self._client

    def save_memory(self, fact: str, tool_context: ToolContext = None) -> str:
        """
        Explicitly save a memory fact to the Memory Bank.

        Args:
            fact: The fact to remember (e.g., "Sarah from work loves hiking")

        Returns:
            Status message indicating success or failure

        Note: Requires user_id from tool_context for proper memory scoping.
        """
        scope, err = self._require_scope(tool_context, "save memory")
        if err:
            return err

        try:

            # Generate memory with proper user scope
            response = self.client.agent_engines.memories.generate(
                name=self.agent_engine_name,
                direct_memories_source={"direct_memories": [{"fact": fact}]},
                scope=scope
            )

            return f"✓ Saved: '{fact}'"

        except Exception as e:
            return f"Error saving memory: {str(e)}"

    def save_multiple_memories(self, facts: list[str], tool_context: ToolContext = None) -> str:
        """
        Save multiple memory facts at once.

        Args:
            facts: List of facts to remember (comma-separated string or list)

        Returns:
            Status message

        Note: Requires user_id from tool_context for proper memory scoping.
        """
        scope, err = self._require_scope(tool_context, "save memories")
        if err:
            return err

        # Handle both string and list input
        if isinstance(facts, str):
            facts = [f.strip() for f in facts.split(',') if f.strip()]

        try:

            # Build direct memories list
            direct_memories = [{"fact": fact} for fact in facts]

            # Generate memories with proper user scope
            response = self.client.agent_engines.memories.generate(
                name=self.agent_engine_name,
                direct_memories_source={"direct_memories": direct_memories},
                scope=scope
            )

            return f"✓ Saved {len(facts)} memories successfully."

        except Exception as e:
            return f"Error saving memories: {str(e)}"

    def retrieve_memories(
        self,
        search_query: str,
        top_k: int = 5,
        tool_context: ToolContext = None
    ) -> str:
        """
        Retrieve relevant memories using similarity search.

        Args:
            search_query: Query to search for similar memories (e.g., "Sarah's preferences")
            top_k: Number of results to return (default: 5, max recommended: 10)

        Returns:
            Formatted string with retrieved memories or error message

        Note: Use this when automatic memory retrieval doesn't provide enough context
        or when you need to expand search terms. Requires user_id from tool_context.
        """
        scope, err = self._require_scope(tool_context, "retrieve memories")
        if err:
            return err

        try:

            # Retrieve memories using similarity search
            results = self.client.agent_engines.memories.retrieve(
                name=self.agent_engine_name,
                scope=scope,
                similarity_search_params={
                    "search_query": search_query,
                    "top_k": min(top_k, 10)  # Cap at 10 for performance
                }
            )

            # Format results
            memories_list = list(results)

            if not memories_list:
                return f"No memories found matching '{search_query}'"

            formatted_results = [f"Found {len(memories_list)} relevant memories for '{search_query}':\n"]

            for i, retrieved_mem in enumerate(memories_list, 1):
                fact = retrieved_mem.memory.fact
                distance = getattr(retrieved_mem, 'distance', 'N/A')
                formatted_results.append(f"{i}. {fact} (similarity: {distance})")

            return "\n".join(formatted_results)

        except Exception as e:
            return f"Error retrieving memories: {str(e)}"
