"""Code execution subagent for running Python code.

This agent provides code execution capabilities for tasks like:
- Date calculations (e.g., "next week", "30 days from now")
- Mathematical operations
- Data transformations
- Any Python computation needed by other tools

Uses BuiltInCodeExecutor which requires Gemini 2.0+.
Wrapped with AgentTool to work alongside other tools.
"""
import os
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.code_executors import BuiltInCodeExecutor


def create_code_executor_agent(
    model_name: str = "gemini-2.0-flash",
    project: str = None,
    location: str = None
):
    """Create a code execution agent.

    Args:
        model_name: Model to use (must be Gemini 2.0+ for code execution).
        project: GCP project ID.
        location: GCP location.

    Returns:
        LlmAgent configured with code execution capability.
    """
    use_vertexai = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"

    model = Gemini(
        model_name=model_name,
        vertexai=use_vertexai,
        project=project or os.getenv("GOOGLE_CLOUD_PROJECT"),
        location=location or os.getenv("GOOGLE_CLOUD_LOCATION")
    )

    agent = LlmAgent(
        name="code_executor",
        model=model,
        code_executor=BuiltInCodeExecutor(),
        instruction=(
            "You are a Python code execution assistant. "
            "Execute Python code to perform calculations and return results.\n\n"
            "Common use cases:\n"
            "- Date calculations: Calculate dates like 'next Monday', 'in 2 weeks', etc.\n"
            "- Math: Perform calculations, conversions, etc.\n"
            "- Data processing: Transform or analyze data.\n\n"
            "Always return clear, formatted results that can be used by other tools.\n\n"
            "If you are unable to fulfill the user's request, or if you determine that you are not the optimal agent to handle it, "
            "you must explicitly return control to the parent agent explaining why."
        )
    )
    return agent
