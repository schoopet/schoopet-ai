"""Async Agent - Factory for creating task-specific async agents.

This module provides async agents that execute different task types:
- research: In-depth research with multiple searches
- analysis: Data analysis and pattern recognition
- reminder: Scheduled reminders
- notification: General notifications
"""
import logging
import asyncio
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AsyncAgent:
    """Base async agent for task execution."""

    def __init__(
        self,
        task_type: str,
        project: str,
        location: str,
        agent_engine_id: str = None,
        collect_memories: bool = False,
        preloaded_memories: List[str] = None,
    ):
        """Initialize the async agent.

        Args:
            task_type: Type of task (research, analysis, reminder, notification)
            project: GCP project ID
            location: GCP location
            agent_engine_id: Optional Agent Engine ID for Vertex AI
            collect_memories: Whether to collect memories during execution
            preloaded_memories: Pre-loaded memories for readonly mode
        """
        self.task_type = task_type
        self.project = project
        self.location = location
        self.agent_engine_id = agent_engine_id
        self.collect_memories = collect_memories
        self.preloaded_memories = preloaded_memories or []
        self._collected_memories: List[str] = []

        # Lazy-loaded clients
        self._vertex_client = None

    def _get_vertex_client(self):
        """Get Vertex AI client, initializing lazily."""
        if self._vertex_client is None:
            from vertexai import Client
            self._vertex_client = Client(
                project=self.project,
                location=self.location
            )
        return self._vertex_client

    def _create_agent(self, instruction: str):
        """Create an ADK LlmAgent with the given instruction."""
        import os
        from functools import cached_property
        from google.adk.agents.llm_agent import LlmAgent
        from google.adk.models.google_llm import Gemini as _BaseGemini
        from google.genai import types

        class GlobalGemini(_BaseGemini):
            @cached_property
            def api_client(self):
                from google.genai import Client
                return Client(
                    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    location="global",
                    http_options=types.HttpOptions(
                        headers=self._tracking_headers(),
                        retry_options=self.retry_options,
                    ),
                )

        # Initialize Model (gemini-3-pro-preview requires the global endpoint)
        model = GlobalGemini(model="gemini-3-pro-preview")

        agent = LlmAgent(
            name=f"async-{self.task_type}-agent",
            model=model,
            tools=[],  # No tools for now
            instruction=instruction,
        )
        return agent

    def _get_system_instruction(self) -> str:
        """Get task-type specific system instruction."""
        base_instruction = (
            "You are an async task agent. Execute the given task and provide "
            "a concise, helpful result. Keep responses under 1000 characters "
            "when possible as they will be delivered via SMS."
        )

        type_instructions = {
            "research": (
                f"{base_instruction}\n\n"
                "You are a research agent. Your job is to:\n"
                "1. Thoroughly research the given topic\n"
                "2. Gather relevant information from multiple angles\n"
                "3. Synthesize findings into a clear, organized summary\n"
                "4. Include key facts, recommendations, and sources when relevant"
            ),
            "analysis": (
                f"{base_instruction}\n\n"
                "You are an analysis agent. Your job is to:\n"
                "1. Analyze the provided data or context\n"
                "2. Identify patterns, insights, and key findings\n"
                "3. Provide actionable recommendations\n"
                "4. Present results clearly and concisely"
            ),
            "reminder": (
                f"{base_instruction}\n\n"
                "You are a reminder agent. Your job is to:\n"
                "1. Deliver the reminder message clearly\n"
                "2. Include any relevant context or details\n"
                "3. Be friendly and helpful\n"
                "4. Keep the message brief and to the point"
            ),
            "notification": (
                f"{base_instruction}\n\n"
                "You are a notification agent. Your job is to:\n"
                "1. Deliver the notification clearly\n"
                "2. Provide any necessary context\n"
                "3. Be informative but concise"
            ),
        }

        return type_instructions.get(self.task_type, base_instruction)

    async def execute(self, prompt: str, context: Dict[str, Any] = None) -> str:
        """Execute the task and return the result.

        Args:
            prompt: The task prompt/instruction
            context: Additional context for execution

        Returns:
            The task result as a string
        """
        try:
            # Create agent with system instruction
            system_instruction = self._get_system_instruction()
            agent = self._create_agent(system_instruction)

            # Build user message with context
            user_message = self._build_user_message(prompt, context)

            # Execute agent (using to_thread for blocking call)
            response = await asyncio.to_thread(agent.query, user_message)

            result = ""
            if hasattr(response, "text"):
                result = response.text
            elif hasattr(response, "content"):
                 # Handle response.content if it's an object with parts
                 if hasattr(response.content, "parts"):
                     for part in response.content.parts:
                         if hasattr(part, "text"):
                             result += part.text
                 else:
                     result = str(response.content)
            else:
                result = str(response)

            # Collect memories if enabled
            if self.collect_memories:
                self._extract_and_collect_memories(result)

            return result

        except Exception as e:
            logger.exception(f"Task execution failed: {e}")
            raise

    async def execute_with_memory_collection(
        self,
        prompt: str,
        context: Dict[str, Any] = None
    ) -> Tuple[str, List[str]]:
        """Execute task and return result with collected memories.

        Used for isolated memory mode where memories are synced on completion.

        Args:
            prompt: The task prompt/instruction
            context: Additional context for execution

        Returns:
            Tuple of (result, collected_memories)
        """
        self.collect_memories = True
        self._collected_memories = []

        result = await self.execute(prompt, context)

        return result, self._collected_memories

    def _build_user_message(self, prompt: str, context: Dict[str, Any] = None) -> str:
        """Build the user message with context."""
        parts = []

        # Add preloaded memories if available
        if self.preloaded_memories:
            parts.append("## Relevant Context from User's Memory:")
            for memory in self.preloaded_memories:
                parts.append(f"- {memory}")

        # Add task context if provided
        if context:
            parts.append("\n## Additional Context:")
            for key, value in context.items():
                parts.append(f"- {key}: {value}")

        # Add the actual task prompt
        parts.append(f"\n## Task:\n{prompt}")

        return "\n".join(parts)

    def _extract_and_collect_memories(self, result: str):
        """Extract potential memories from the result.

        This is a simple extraction - in production, you might use
        an LLM to identify key facts worth remembering.
        """
        # For now, just collect the result as a potential memory
        # In a more sophisticated implementation, this could:
        # 1. Use an LLM to extract key facts
        # 2. Identify entities and relationships
        # 3. Structure memories for better retrieval

        if len(result) > 50:  # Only collect substantial results
            summary = f"Task result ({self.task_type}): {result[:500]}"
            self._collected_memories.append(summary)


def create_async_agent(
    task_type: str,
    project: str,
    location: str,
    agent_engine_id: str = None,
    collect_memories: bool = False,
    preloaded_memories: List[str] = None,
) -> AsyncAgent:
    """Factory function to create an async agent.

    Args:
        task_type: Type of task (research, analysis, reminder, notification)
        project: GCP project ID
        location: GCP location
        agent_engine_id: Optional Agent Engine ID
        collect_memories: Whether to collect memories during execution
        preloaded_memories: Pre-loaded memories for readonly mode

    Returns:
        Configured AsyncAgent instance
    """
    return AsyncAgent(
        task_type=task_type,
        project=project,
        location=location,
        agent_engine_id=agent_engine_id,
        collect_memories=collect_memories,
        preloaded_memories=preloaded_memories,
    )
