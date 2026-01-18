"""Unit tests for MemoryTool."""
import pytest
from unittest.mock import MagicMock, patch
from agents.schoopet.memory_tool import MemoryTool
from google.adk.tools import ToolContext

# Sample data
PROJECT_ID = "test-project"
LOCATION = "us-central1"
AGENT_ENGINE_ID = "test-engine-id"
USER_ID = "+14155551234"
MEMORY_FACT = "Test fact"

@pytest.fixture
def mock_env():
    """Mock environment variables."""
    with patch("os.getenv") as mock_getenv:
        def getenv_side_effect(key, default=None):
            env_vars = {
                "GOOGLE_CLOUD_PROJECT": PROJECT_ID,
                "GOOGLE_CLOUD_LOCATION": LOCATION,
                "GOOGLE_CLOUD_AGENT_ENGINE_ID": AGENT_ENGINE_ID,
            }
            return env_vars.get(key, default)
        
        mock_getenv.side_effect = getenv_side_effect
        yield mock_getenv

@pytest.fixture
def memory_tool(mock_env):
    """Create a MemoryTool instance with mocked client."""
    with patch("vertexai.Client") as mock_client_cls:
        tool = MemoryTool()
        # Trigger lazy init
        _ = tool.client
        
        # Inject mock
        tool._client = mock_client_cls.return_value
        
        yield tool

@pytest.fixture
def tool_context():
    """Create a mock ToolContext."""
    context = MagicMock(spec=ToolContext)
    context.user_id = USER_ID
    return context

class TestMemoryTool:
    """Tests for MemoryTool class."""

    def test_initialization(self, memory_tool):
        """Should initialize with correct configuration."""
        assert memory_tool.project_id == PROJECT_ID
        assert memory_tool.location == LOCATION
        assert memory_tool.agent_engine_id == AGENT_ENGINE_ID
        assert memory_tool.agent_engine_name == f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{AGENT_ENGINE_ID}"

    def test_initialization_missing_env(self):
        """Should warn if env vars missing."""
        with patch("os.getenv", return_value=None):
            tool = MemoryTool()
            assert tool.client is None
            assert tool.agent_engine_name is None

    def test_save_memory_success(self, memory_tool, tool_context):
        """Should save memory successfully."""
        # Mock API response
        mock_memories = memory_tool.client.agent_engines.memories
        
        result = memory_tool.save_memory(MEMORY_FACT, tool_context)
        
        assert "Saved" in result
        mock_memories.generate.assert_called_once_with(
            name=memory_tool.agent_engine_name,
            direct_memories_source={"direct_memories": [{"fact": MEMORY_FACT}]},
            scope={"user_id": USER_ID}
        )

    def test_save_memory_no_context(self, memory_tool):
        """Should fail if tool context is missing."""
        result = memory_tool.save_memory(MEMORY_FACT, None)
        assert "ERROR: Cannot save memory" in result
        memory_tool.client.agent_engines.memories.generate.assert_not_called()

    def test_save_memory_no_user_id(self, memory_tool):
        """Should fail if user_id is missing from context."""
        context = MagicMock(spec=ToolContext)
        context.user_id = None
        
        result = memory_tool.save_memory(MEMORY_FACT, context)
        assert "ERROR: Cannot save memory" in result
        memory_tool.client.agent_engines.memories.generate.assert_not_called()

    def test_save_multiple_memories_list(self, memory_tool, tool_context):
        """Should save list of memories."""
        facts = ["Fact 1", "Fact 2"]
        mock_memories = memory_tool.client.agent_engines.memories
        
        result = memory_tool.save_multiple_memories(facts, tool_context)
        
        assert "Saved 2 memories" in result
        mock_memories.generate.assert_called_once_with(
            name=memory_tool.agent_engine_name,
            direct_memories_source={"direct_memories": [{"fact": "Fact 1"}, {"fact": "Fact 2"}]},
            scope={"user_id": USER_ID}
        )

    def test_save_multiple_memories_string(self, memory_tool, tool_context):
        """Should split comma-separated string."""
        facts = "Fact 1, Fact 2 "
        mock_memories = memory_tool.client.agent_engines.memories
        
        result = memory_tool.save_multiple_memories(facts, tool_context)
        
        assert "Saved 2 memories" in result
        mock_memories.generate.assert_called_once_with(
            name=memory_tool.agent_engine_name,
            direct_memories_source={"direct_memories": [{"fact": "Fact 1"}, {"fact": "Fact 2"}]},
            scope={"user_id": USER_ID}
        )

    def test_retrieve_memories_success(self, memory_tool, tool_context):
        """Should retrieve and format memories."""
        # Mock search results
        mock_result1 = MagicMock()
        mock_result1.memory.fact = "Fact 1"
        mock_result1.distance = 0.1
        
        mock_result2 = MagicMock()
        mock_result2.memory.fact = "Fact 2"
        mock_result2.distance = 0.2
        
        mock_memories = memory_tool.client.agent_engines.memories
        mock_memories.retrieve.return_value = [mock_result1, mock_result2]
        
        result = memory_tool.retrieve_memories("query", tool_context=tool_context)
        
        assert "Found 2 relevant memories" in result
        assert "1. Fact 1 (similarity: 0.1)" in result
        assert "2. Fact 2 (similarity: 0.2)" in result
        
        mock_memories.retrieve.assert_called_once_with(
            name=memory_tool.agent_engine_name,
            scope={"user_id": USER_ID},
            similarity_search_params={"search_query": "query", "top_k": 5}
        )

    def test_retrieve_memories_empty(self, memory_tool, tool_context):
        """Should handle empty results."""
        memory_tool.client.agent_engines.memories.retrieve.return_value = []
        
        result = memory_tool.retrieve_memories("query", tool_context=tool_context)
        
        assert "No memories found" in result

    def test_retrieve_memories_error(self, memory_tool, tool_context):
        """Should handle API errors."""
        memory_tool.client.agent_engines.memories.retrieve.side_effect = Exception("API Error")
        
        result = memory_tool.retrieve_memories("query", tool_context=tool_context)
        
        assert "Error retrieving memories: API Error" in result
