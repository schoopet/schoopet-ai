"""Unit tests for TaskWorker and AsyncTaskDocument model."""
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
        "PERSONAL_AGENT_ENGINE_ID": "personal-engine-1",
        "TEAM_AGENT_ENGINE_ID": "team-engine-1",
        "SMS_GATEWAY_URL": "http://gateway"
    }):
        worker._ensure_initialized()
        worker._firestore_client = mock_firestore
    return worker


def _mock_adk_app_with_response(text: str):
    """Create a mock ADK app that returns a streaming response with the given text."""
    mock_adk_app = MagicMock()

    # Mock async_create_session
    async def mock_create_session(**kwargs):
        return {"id": "session-123"}
    mock_adk_app.async_create_session = mock_create_session

    # Mock async_stream_query as an async generator
    async def mock_stream_query(**kwargs):
        yield {"content": {"parts": [{"text": text}]}}
    mock_adk_app.async_stream_query = mock_stream_query

    return mock_adk_app


class TestTaskWorker:
    """Tests for TaskWorker class."""

    @pytest.mark.asyncio
    async def test_execute_task_success(self, task_worker, mock_firestore):
        """Should execute task via Agent Engine and update result."""
        # Mock task document
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",
            "agent_type": "personal",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock Agent Engine
        mock_adk_app = _mock_adk_app_with_response("AI Result")
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client

        # Mock notification
        task_worker._notify_for_review = AsyncMock()

        result = await task_worker.execute_task(TASK_ID)

        assert result["success"] is True

        # Verify status updates
        doc_ref = mock_firestore.collection.return_value.document.return_value
        # Should update to running
        running_update = doc_ref.update.call_args_list[0]
        assert running_update.args[0] == {"status": "running", "started_at": ANY}
        assert "option" in running_update.kwargs
        # Should update result
        doc_ref.update.assert_any_call({
            "status": "awaiting_review",
            "result": "AI Result",
            "completed_at": ANY
        })

        # Verify notification
        task_worker._notify_for_review.assert_awaited_once_with(
            task_id=TASK_ID,
            user_id=USER_ID,
            agent_type="personal",
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
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",
            "agent_type": "personal",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock Agent Engine that raises
        mock_adk_app = MagicMock()
        async def mock_create_session(**kwargs):
            raise Exception("Agent Error")
        mock_adk_app.async_create_session = mock_create_session

        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client

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
        """Should execute revision with feedback in prompt."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "revision_requested",
            "task_type": "research",
            "instruction": "Research AI",
            "result": "Old Result",
            "revision_feedback": "Fix it",
            "agent_type": "personal",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock Agent Engine
        mock_adk_app = _mock_adk_app_with_response("Revised Result")
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client

        task_worker._notify_for_review = AsyncMock()

        result = await task_worker.execute_task(TASK_ID)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_task_defaults_to_personal_engine(self, task_worker, mock_firestore):
        """Tasks without agent_type should default to personal engine."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "reminder",
            "instruction": "Remind user",
            # No agent_type field — should default to personal
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        mock_adk_app = _mock_adk_app_with_response("Reminder sent")
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client

        task_worker._notify_for_review = AsyncMock()

        await task_worker.execute_task(TASK_ID)

        engine_name = mock_vertex_client.agent_engines.get.call_args[1]["name"]
        assert "personal-engine-1" in engine_name

    @pytest.mark.asyncio
    async def test_execute_task_missing_engine_id_fails(self, mock_firestore):
        """Should fail with clear error when engine ID is not configured."""
        worker = TaskWorker()
        with patch.dict("os.environ", {
            "GOOGLE_CLOUD_PROJECT": PROJECT_ID,
            # No engine IDs set
            "SMS_GATEWAY_URL": "http://gateway"
        }):
            worker._ensure_initialized()
            worker._firestore_client = mock_firestore
            # Explicitly clear engine IDs
            worker._personal_engine_id = None
            worker._team_engine_id = None

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",
            "agent_type": "personal",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        worker._notify_for_review = AsyncMock()

        result = await worker.execute_task(TASK_ID)

        assert result["success"] is False
        assert "No engine ID configured" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_task_skips_lost_claim(self, task_worker, mock_firestore):
        """Should skip execution if another worker claimed the task first."""
        initial_doc = MagicMock()
        initial_doc.exists = True
        initial_doc.update_time = object()
        initial_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",
            "agent_type": "personal",
        }

        claimed_doc = MagicMock()
        claimed_doc.exists = True
        claimed_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "running",
            "task_type": "research",
            "instruction": "Research AI",
            "agent_type": "personal",
        }

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.get.side_effect = [initial_doc, claimed_doc]
        doc_ref.update.side_effect = Exception("precondition failed")

        task_worker._execute_task = AsyncMock()
        task_worker._notify_for_review = AsyncMock()

        result = await task_worker.execute_task(TASK_ID)

        assert result["success"] is True
        assert result["message"] == "Task already in status: running"
        task_worker._execute_task.assert_not_awaited()
        task_worker._notify_for_review.assert_not_awaited()
