"""Unit tests for AsyncAgent with ADK."""
import pytest
from unittest.mock import MagicMock, patch
import sys
from src.async_agent import create_async_agent

# Constants
PROJECT_ID = "test-project"
LOCATION = "us-central1"


@pytest.fixture
def mock_adk():
    """Mock google.adk and google.genai components used by async_agent.execute()."""
    mock_runner_mod = MagicMock()
    mock_sessions_mod = MagicMock()
    mock_llm_agent_mod = MagicMock()
    mock_google_llm_mod = MagicMock()
    mock_genai_types_mod = MagicMock()
    mock_genai_mod = MagicMock()
    # `from google.genai import types` resolves to the `types` attr of the genai module mock.
    # Pin it to the same object as sys.modules["google.genai.types"] so both paths agree.
    mock_genai_mod.types = mock_genai_types_mod

    with patch.dict(sys.modules, {
        "google.adk.runners": mock_runner_mod,
        "google.adk.sessions": mock_sessions_mod,
        "google.adk.agents.llm_agent": mock_llm_agent_mod,
        "google.adk.models.google_llm": mock_google_llm_mod,
        "google.genai": mock_genai_mod,
        "google.genai.types": mock_genai_types_mod,
    }):
        yield {
            "Runner": mock_runner_mod.Runner,
            "InMemorySessionService": mock_sessions_mod.InMemorySessionService,
            "LlmAgent": mock_llm_agent_mod.LlmAgent,
            "Gemini": mock_google_llm_mod.Gemini,
            "types": mock_genai_types_mod,
        }


def _make_final_event(text: str) -> MagicMock:
    """Build a mock ADK event that looks like a final response with text."""
    mock_part = MagicMock()
    mock_part.text = text
    mock_event = MagicMock()
    mock_event.is_final_response.return_value = True
    mock_event.content.parts = [mock_part]
    return mock_event


def _setup_runner(mock_adk, event: MagicMock) -> MagicMock:
    """Wire up InMemorySessionService + Runner mocks and return the runner instance."""
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_adk["InMemorySessionService"].return_value.create_session.return_value = mock_session

    async def fake_run_async(**kwargs):
        yield event

    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = fake_run_async
    mock_adk["Runner"].return_value = mock_runner_instance
    return mock_runner_instance


@pytest.mark.asyncio
async def test_execute_success(mock_adk):
    """Should execute task via Runner and return streamed final-response text."""
    agent = create_async_agent(task_type="research", project=PROJECT_ID, location=LOCATION)
    _setup_runner(mock_adk, _make_final_event("Research Result"))

    result = await agent.execute("Research AI")

    assert result == "Research Result"

    # LlmAgent must have been constructed with the right instruction
    mock_adk["LlmAgent"].assert_called_once()
    call_kwargs = mock_adk["LlmAgent"].call_args[1]
    assert "You are a research agent" in call_kwargs["instruction"]

    # Runner must have been constructed with the agent
    mock_adk["Runner"].assert_called_once()


@pytest.mark.asyncio
async def test_execute_with_context(mock_adk):
    """Should include context and memories in the user message sent to the Runner."""
    agent = create_async_agent(
        task_type="analysis",
        project=PROJECT_ID,
        location=LOCATION,
        preloaded_memories=["Memory 1"],
    )
    _setup_runner(mock_adk, _make_final_event("Analysis Result"))

    context = {"data": "some data"}
    result = await agent.execute("Analyze", context=context)

    assert result == "Analysis Result"

    # Inspect the text passed to types.Part to verify message structure
    types_mod = mock_adk["types"]
    part_call = types_mod.Part.call_args
    user_message = part_call.kwargs.get("text") or part_call.args[0]
    assert "## Relevant Context from User's Memory:" in user_message
    assert "- Memory 1" in user_message
    assert "## Additional Context:" in user_message
    assert "- data: some data" in user_message
    assert "## Task:\nAnalyze" in user_message
