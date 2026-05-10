"""Unit tests for TaskWorker and AsyncTaskDocument model."""
import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from src.worker import TaskWorker, _build_allowed_resource_state

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
    # google.cloud and google namespace packages must also be present so that
    # lazy `from google.cloud import firestore` imports inside the worker succeed.
    mock_google = MagicMock()
    mock_google_cloud = MagicMock()
    mock_google_cloud.firestore = mock_module
    with patch.dict("sys.modules", {
        "google": mock_google,
        "google.cloud": mock_google_cloud,
        "google.cloud.firestore": mock_module,
    }):
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
        "PERSONAL_AGENT_ENGINE_ID": "personal-engine-1",
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


class TestBuildAllowedResourceState:
    """Tests for _build_allowed_resource_state helper."""

    def test_empty_input_returns_empty(self):
        assert _build_allowed_resource_state([]) == {}

    def test_maps_single_id(self):
        state = _build_allowed_resource_state(["abc"])
        assert state == {"_resource_confirmed_abc": True}

    def test_maps_multiple_ids(self):
        state = _build_allowed_resource_state(["sheet-1", "doc-1", "folder-99"])
        assert state == {
            "_resource_confirmed_sheet-1": True,
            "_resource_confirmed_doc-1": True,
            "_resource_confirmed_folder-99": True,
        }

    def test_maps_mixed_ids(self):
        state = _build_allowed_resource_state(["s1", "d1", "f1"])
        assert state == {
            "_resource_confirmed_s1": True,
            "_resource_confirmed_d1": True,
            "_resource_confirmed_f1": True,
        }


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

        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock Agent Engine
        mock_adk_app = _mock_adk_app_with_response("AI Result")
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client

        # Mock notification
        task_worker._notify_user = AsyncMock()

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
            "status": "completed",
            "result": "AI Result",
            "completed_at": ANY,
        })

        # Verify notification
        task_worker._notify_user.assert_awaited_once_with(
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
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",

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

        task_worker._notify_user = AsyncMock()

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
    async def test_execute_task_uses_personal_engine(self, task_worker, mock_firestore):
        """Tasks always execute on the personal engine."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "reminder",
            "instruction": "Remind user",
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        mock_adk_app = _mock_adk_app_with_response("Reminder sent")
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client

        task_worker._notify_user = AsyncMock()

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

        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        worker._notify_user = AsyncMock()

        result = await worker.execute_task(TASK_ID)

        assert result["success"] is False
        assert "No engine ID configured" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_task_passes_allowed_resource_state(self, task_worker, mock_firestore):
        """Should seed session state with pre-approved resource IDs."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "DEEP_RESEARCH_TASK: find restaurants",

            "allowed_resource_ids": ["sheet-abc", "doc-xyz"],
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        captured_kwargs = {}

        async def mock_create_session(**kwargs):
            captured_kwargs.update(kwargs)
            return {"id": "session-123"}

        mock_adk_app = _mock_adk_app_with_response("Research done")
        mock_adk_app.async_create_session = mock_create_session
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client
        task_worker._notify_user = AsyncMock()

        await task_worker.execute_task(TASK_ID)

        assert captured_kwargs.get("state") == {
            "_resource_confirmed_sheet-abc": True,
            "_resource_confirmed_doc-xyz": True,
        }

    @pytest.mark.asyncio
    async def test_execute_task_logs_full_offline_prompt(self, task_worker, mock_firestore, caplog):
        """Should log the full prompt sent to Agent Engine for offline debugging."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Line one\nLine two with sheet details",
            "context": {"source": "unit-test"},
        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        mock_adk_app = _mock_adk_app_with_response("Research done")
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client
        task_worker._notify_user = AsyncMock()
        caplog.set_level(logging.INFO, logger="src.worker")

        await task_worker.execute_task(TASK_ID)

        assert "Offline task prompt for task_id=task-123 user=user-123 session_id=session-123:" in caplog.text
        assert "Execute this research task:" in caplog.text
        assert "Line one\nLine two with sheet details" in caplog.text
        assert "Additional context:\n  source: unit-test" in caplog.text

    @pytest.mark.asyncio
    async def test_execute_task_no_allowed_resources_passes_empty_state(self, task_worker, mock_firestore):
        """Should pass empty state dict when no allowed resources are set."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.update_time = object()
        mock_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "pending",
            "task_type": "research",
            "instruction": "Research AI",

        }
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        captured_kwargs = {}

        async def mock_create_session(**kwargs):
            captured_kwargs.update(kwargs)
            return {"id": "session-123"}

        mock_adk_app = _mock_adk_app_with_response("Result")
        mock_adk_app.async_create_session = mock_create_session
        mock_vertex_client = MagicMock()
        mock_vertex_client.agent_engines.get.return_value = mock_adk_app
        task_worker._vertex_client = mock_vertex_client
        task_worker._notify_user = AsyncMock()

        await task_worker.execute_task(TASK_ID)

        assert captured_kwargs.get("state") == {}

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

        }

        claimed_doc = MagicMock()
        claimed_doc.exists = True
        claimed_doc.to_dict.return_value = {
            "task_id": TASK_ID,
            "user_id": USER_ID,
            "status": "running",
            "task_type": "research",
            "instruction": "Research AI",

        }

        doc_ref = mock_firestore.collection.return_value.document.return_value
        doc_ref.get.side_effect = [initial_doc, claimed_doc]
        doc_ref.update.side_effect = Exception("precondition failed")

        task_worker._execute_task = AsyncMock()
        task_worker._notify_user = AsyncMock()

        result = await task_worker.execute_task(TASK_ID)

        assert result["success"] is True
        assert result["message"] == "Task already in status: running"
        task_worker._execute_task.assert_not_awaited()
        task_worker._notify_user.assert_not_awaited()
