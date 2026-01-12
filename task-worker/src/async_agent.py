"""Async Agent - Factory for creating task-specific async agents.

This module provides async agents that execute different task types:
- research: In-depth research with multiple searches
- analysis: Data analysis and pattern recognition
- reminder: Scheduled reminders
- notification: General notifications
"""
import logging
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
        self._model = None

    def _get_vertex_client(self):
        """Get Vertex AI client, initializing lazily."""
        if self._vertex_client is None:
            from vertexai import Client
            self._vertex_client = Client(
                project=self.project,
                location=self.location
            )
        return self._vertex_client

    def _get_model(self):
        """Get the Gemini model for task execution."""
        if self._model is None:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            vertexai.init(project=self.project, location=self.location)
            self._model = GenerativeModel("gemini-2.0-flash-001")
        return self._model

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
            model = self._get_model()

            # Build the full prompt with context
            full_prompt = self._build_full_prompt(prompt, context)

            # Generate response
            response = model.generate_content(
                full_prompt,
                generation_config={
                    "max_output_tokens": 2048,
                    "temperature": 0.7,
                }
            )

            result = response.text

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

    def _build_full_prompt(self, prompt: str, context: Dict[str, Any] = None) -> str:
        """Build the full prompt with system instruction and context."""
        parts = [self._get_system_instruction()]

        # Add preloaded memories if available
        if self.preloaded_memories:
            parts.append("\n## Relevant Context from User's Memory:")
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
