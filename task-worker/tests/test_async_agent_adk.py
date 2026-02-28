"""Unit tests for AsyncAgent with ADK."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
from src.async_agent import create_async_agent

# Constants
PROJECT_ID = "test-project"
LOCATION = "us-central1"

@pytest.fixture
def mock_vertex():
    """Mock Vertex AI Client."""
    with patch("vertexai.Client") as mock_client:
        yield mock_client

@pytest.fixture
def mock_adk():
    """Mock google.adk components."""
    # Since we import inside the method, we need to patch sys.modules or the specific import path
    # But patching sys.modules for google.adk might be tricky if it's already imported.
    # We will patch the classes where they are imported FROM.
    # In async_agent.py, we do: from google.adk.agents.llm_agent import LlmAgent

    # We can mock the modules in sys.modules before they are imported
    mock_llm_agent_mod = MagicMock()
    mock_google_llm_mod = MagicMock()

    with patch.dict(sys.modules, {
        "google.adk.agents.llm_agent": mock_llm_agent_mod,
        "google.adk.models.google_llm": mock_google_llm_mod,
    }):
        yield {
            "LlmAgent": mock_llm_agent_mod.LlmAgent,
            "Gemini": mock_google_llm_mod.Gemini
        }

@pytest.mark.asyncio
async def test_execute_success(mock_adk, mock_vertex):
    """Should execute task using LlmAgent."""
    agent = create_async_agent(
        task_type="research",
        project=PROJECT_ID,
        location=LOCATION
    )

    # Mock LlmAgent instance
    mock_llm_instance = mock_adk["LlmAgent"].return_value
    # Mock query response
    mock_response = MagicMock()
    mock_response.text = "Research Result"
    mock_llm_instance.query.return_value = mock_response

    # Execute
    result = await agent.execute("Research AI")

    assert result == "Research Result"

    # Verify Gemini creation
    mock_adk["Gemini"].assert_called_with(
        model_name="gemini-3-flash-preview",
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION
    )

    # Verify LlmAgent creation
    mock_adk["LlmAgent"].assert_called_once()
    call_kwargs = mock_adk["LlmAgent"].call_args[1]
    assert call_kwargs["model"] == mock_adk["Gemini"].return_value
    assert "You are a research agent" in call_kwargs["instruction"]

    # Verify query
    mock_llm_instance.query.assert_called_once()
    query_arg = mock_llm_instance.query.call_args[0][0]
    assert "## Task:\nResearch AI" in query_arg
    assert "## Relevant Context" not in query_arg # No context provided

@pytest.mark.asyncio
async def test_execute_with_context(mock_adk, mock_vertex):
    """Should include context and memories in query."""
    agent = create_async_agent(
        task_type="analysis",
        project=PROJECT_ID,
        location=LOCATION,
        preloaded_memories=["Memory 1"]
    )

    mock_llm_instance = mock_adk["LlmAgent"].return_value
    mock_response = MagicMock()
    mock_response.text = "Analysis Result"
    mock_llm_instance.query.return_value = mock_response

    context = {"data": "some data"}
    result = await agent.execute("Analyze", context=context)

    assert result == "Analysis Result"

    # Verify query content
    query_arg = mock_llm_instance.query.call_args[0][0]
    assert "## Relevant Context from User's Memory:" in query_arg
    assert "- Memory 1" in query_arg
    assert "## Additional Context:" in query_arg
    assert "- data: some data" in query_arg
    assert "## Task:\nAnalyze" in query_arg
