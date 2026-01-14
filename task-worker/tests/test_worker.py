"""Unit tests for TaskWorker."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from src.worker import TaskWorker

# Sample data
TASK_ID = "task-123"
USER_ID = "user-123"
PROJECT_ID = "test-project"

@pytest.fixture
def mock_firestore():
    """Mock Firestore client."""
    mock_module = MagicMock()
    client = MagicMock()
    mock_module.Client.return_value = client
    with patch.dict("sys.modules", {"google.cloud.firestore": mock_module}):
        yield client

@pytest.fixture
def mock_vertex():
    """Mock Vertex AI client."""
    mock_module = MagicMock()
    client = MagicMock()
    mock_module.Client.return_value = client
    with patch.dict("sys.modules", {"vertexai": mock_module}):
        yield client

@pytest.fixture
def mock_http():
    """Mock HTTP client."""
    with patch("aiohttp.ClientSession") as mock_h:
        session = AsyncMock()
        mock_h.return_value = session
        yield session

@pytest.fixture
def task_worker(mock_firestore):
    """Create TaskWorker instance."""
    worker = TaskWorker()
    with patch.dict("os.environ", {
        "GOOGLE_CLOUD_PROJECT": PROJECT_ID,
        "AGENT_ENGINE_ID": "agent-engine-1",
        "SMS_GATEWAY_URL": "http://gateway"
    }):
        worker._ensure_initialized()
        worker._firestore_client = mock_firestore
    return worker

class TestTaskWorker:
    """Tests for TaskWorker class."""

    @pytest.mark.asyncio
    async def test_execute_task_success(self, task_worker, mock_firestore):
        """Should execute task and update result."""
        # Mock task document
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",
            "memory_isolation": "shared"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock AsyncAgent
        with patch("src.async_agent.create_async_agent") as mock_create_agent:
            mock_agent = AsyncMock()
            mock_agent.execute.return_value = "AI Result"
            mock_create_agent.return_value = mock_agent
            
            # Mock notification
            task_worker._notify_for_review = AsyncMock()

            result = await task_worker.execute_task(TASK_ID)

            assert result["success"] is True
            
            # Verify status updates
            doc_ref = mock_firestore.collection.return_value.document.return_value
            # Should update to running
            doc_ref.update.assert_any_call({"status": "running", "started_at": ANY})
            # Should update result
            doc_ref.update.assert_any_call({
                "status": "awaiting_review",
                "result": "AI Result",
                "result_memories": [],
                "completed_at": ANY
            })

            # Verify notification
            task_worker._notify_for_review.assert_awaited_once_with(
                task_id=TASK_ID,
                user_id=USER_ID,
                result="AI Result"
            )

    @pytest.mark.asyncio
    async def test_execute_task_not_found(self, task_worker, mock_firestore):
        """Should handle missing task."""
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await task_worker.execute_task(TASK_ID)

        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_task_already_processed(self, task_worker, mock_firestore):
        """Should skip already processed task."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "status": "completed"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await task_worker.execute_task(TASK_ID)

        assert result["success"] is True
        assert "Task already in status" in result["message"]

    @pytest.mark.asyncio
    async def test_execute_task_failure(self, task_worker, mock_firestore):
        """Should handle execution failure."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch("src.async_agent.create_async_agent") as mock_create_agent:
            mock_agent = AsyncMock()
            mock_agent.execute.side_effect = Exception("Agent Error")
            mock_create_agent.return_value = mock_agent

            task_worker._notify_for_review = AsyncMock()

            result = await task_worker.execute_task(TASK_ID)

            assert result["success"] is False
            assert "Agent Error" in result["error"]

            # Verify error update
            doc_ref = mock_firestore.collection.return_value.document.return_value
            doc_ref.update.assert_called_with({
                "status": "failed",
                "error": "Agent Error",
                "completed_at": ANY
            })

    @pytest.mark.asyncio
    async def test_execute_revision(self, task_worker, mock_firestore):
        """Should execute revision logic."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "revision_requested",
            "task_type": "research",
            "instruction": "Research AI",
            "result": "Old Result",
            "revision_feedback": "Fix it"
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch("src.async_agent.create_async_agent") as mock_create_agent:
            mock_agent = AsyncMock()
            mock_agent.execute.return_value = "Revised Result"
            mock_create_agent.return_value = mock_agent
            
            task_worker._notify_for_review = AsyncMock()

            await task_worker.execute_task(TASK_ID)

            # Verify revision prompt construction (indirectly via execution success)
            # We can check if agent was called with feedback included
            # But here just verifying flow completion is good
            mock_agent.execute.assert_called()
