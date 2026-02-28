"""Code execution subagent for running Python code.

This agent provides code execution capabilities for tasks like:
- Date calculations (e.g., "next week", "30 days from now")
- Mathematical operations
- Data transformations
- Any Python computation needed by other tools

Uses BuiltInCodeExecutor which requires Gemini 2.0+.
Wrapped with AgentTool to work alongside other tools.
"""
from google.adk.agents.llm_agent import LlmAgent
from google.adk.code_executors import BuiltInCodeExecutor
from .global_gemini import GlobalGemini


def create_code_executor_agent(
    model_name: str = "gemini-3-flash-preview",
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
    model = GlobalGemini(model=model_name)

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
