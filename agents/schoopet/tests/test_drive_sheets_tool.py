"""Unit tests for DriveTool.save_attachment_to_drive and _save_binary_with_token.

Strategy: pre-populate an InMemoryArtifactService, then wire tool_context.load_artifact
to it — so the tool goes through the real ADK load path without needing GCS.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

from agents.schoopet.drive_sheets_tool import DriveTool

PHONE = "+19494136310"
SESSION_ID = "sess-test"
ARTIFACT_FILENAME = "msg001_resume.pdf"
DRIVE_FILENAME = "resume_john.pdf"
PDF_BYTES = b"%PDF-1.4 content"
SYS_TOKEN = "sys-access-token"
USER_TOKEN = "user-access-token"
DRIVE_FILE_ID = "drive-file-abc"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_drive_response(file_id: str = DRIVE_FILE_ID):
    """Return a mock httpx response that looks like a successful Drive upload."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": file_id}
    resp.raise_for_status = MagicMock()
    return resp


async def _make_artifact_service(
    filename: str = ARTIFACT_FILENAME,
    data: bytes = PDF_BYTES,
    mime_type: str = "application/pdf",
) -> InMemoryArtifactService:
    """Create an InMemoryArtifactService pre-loaded with one artifact."""
    svc = InMemoryArtifactService()
    artifact = types.Part(inline_data=types.Blob(mime_type=mime_type, data=data))
    await svc.save_artifact(
        app_name="coordinator",
        user_id=PHONE,
        session_id=SESSION_ID,
        filename=filename,
        artifact=artifact,
    )
    return svc


def _make_tool_context(svc: InMemoryArtifactService, user_id: str = PHONE) -> AsyncMock:
    """Return a mock ToolContext whose load_artifact delegates to the given service."""
    ctx = AsyncMock()
    ctx.user_id = user_id

    async def load_artifact(filename, version=None):
        return await svc.load_artifact(
            app_name="coordinator",
            user_id=user_id,
            session_id=SESSION_ID,
            filename=filename,
            version=version,
        )

    ctx.load_artifact = load_artifact
    return ctx


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def drive_tool_personal():
    tool = DriveTool.__new__(DriveTool)
    tool._oauth_client = MagicMock()
    tool._access_mode = "personal"
    return tool


@pytest.fixture
def drive_tool_team():
    tool = DriveTool.__new__(DriveTool)
    tool._oauth_client = MagicMock()
    tool._access_mode = "team"
    return tool


# ── save_attachment_to_drive tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_attachment_team_mode_uses_system_token(drive_tool_team):
    """Team-mode DriveTool always uses the workspace_system token."""
    svc = await _make_artifact_service()
    ctx = _make_tool_context(svc)

    drive_tool_team._oauth_client.get_valid_access_token.side_effect = lambda uid, feat: (
        SYS_TOKEN if (uid == "email_system" and feat == "workspace_system") else None
    )

    mock_resp = _make_drive_response()

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = await drive_tool_team.save_attachment_to_drive(
            artifact_filename=ARTIFACT_FILENAME,
            drive_filename=DRIVE_FILENAME,
            tool_context=ctx,
        )

    assert "Saved" in result
    assert DRIVE_FILENAME in result
    assert DRIVE_FILE_ID in result

    post_call = mock_httpx.return_value.__enter__.return_value.post
    headers = post_call.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {SYS_TOKEN}"


@pytest.mark.asyncio
async def test_save_attachment_personal_mode_uses_user_token(drive_tool_personal):
    """Personal-mode DriveTool always uses the user's google-workspace token."""
    svc = await _make_artifact_service()
    ctx = _make_tool_context(svc)

    drive_tool_personal._oauth_client.get_valid_access_token.side_effect = lambda uid, feat: (
        USER_TOKEN if uid == PHONE else None
    )

    mock_resp = _make_drive_response()

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = await drive_tool_personal.save_attachment_to_drive(
            artifact_filename=ARTIFACT_FILENAME,
            drive_filename=DRIVE_FILENAME,
            tool_context=ctx,
        )

    assert "Saved" in result

    post_call = mock_httpx.return_value.__enter__.return_value.post
    headers = post_call.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {USER_TOKEN}"


@pytest.mark.asyncio
async def test_save_attachment_artifact_not_found(drive_tool_personal):
    """Returns error when artifact does not exist in the registry."""
    svc = InMemoryArtifactService()  # empty — nothing saved
    ctx = _make_tool_context(svc)

    result = await drive_tool_personal.save_attachment_to_drive(
        artifact_filename="missing.pdf",
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert "ERROR" in result
    assert "missing.pdf" in result


@pytest.mark.asyncio
async def test_save_attachment_no_user_id(drive_tool_personal):
    """Returns error when tool_context has no user_id."""
    ctx = AsyncMock()
    ctx.user_id = None

    result = await drive_tool_personal.save_attachment_to_drive(
        artifact_filename=ARTIFACT_FILENAME,
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert "ERROR" in result
    assert "user_id" in result


@pytest.mark.asyncio
async def test_save_attachment_no_token_returns_auth_instructions(drive_tool_personal):
    """Returns human-readable instructions when no token is available."""
    svc = await _make_artifact_service()
    ctx = _make_tool_context(svc)

    drive_tool_personal._oauth_client.get_valid_access_token.return_value = None
    drive_tool_personal._oauth_client.get_oauth_link.return_value = "https://example.com/oauth"

    result = await drive_tool_personal.save_attachment_to_drive(
        artifact_filename=ARTIFACT_FILENAME,
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert "https://example.com/oauth" in result


# ── _save_binary_with_token tests ──────────────────────────────────────────────

def test_save_binary_with_token_body_is_bytes(drive_tool_personal):
    """The multipart body sent to Drive is bytes, not str."""
    mock_resp = _make_drive_response()

    with patch("httpx.Client") as mock_httpx:
        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp

        drive_tool_personal._save_binary_with_token(
            token=SYS_TOKEN,
            filename=DRIVE_FILENAME,
            raw_bytes=PDF_BYTES,
            mime_type="application/pdf",
            folder_id="",
        )

    post_call = mock_client.post
    body = post_call.call_args.kwargs.get("content")
    assert isinstance(body, bytes), "body passed to httpx must be bytes"
    assert b"Content-Type: application/pdf" in body
    assert PDF_BYTES in body


def test_save_binary_with_token_includes_filename_in_metadata(drive_tool_personal):
    """The JSON metadata part contains the correct filename."""
    mock_resp = _make_drive_response()

    with patch("httpx.Client") as mock_httpx:
        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp

        drive_tool_personal._save_binary_with_token(
            token=SYS_TOKEN,
            filename=DRIVE_FILENAME,
            raw_bytes=PDF_BYTES,
            mime_type="application/pdf",
            folder_id="",
        )

    body = mock_client.post.call_args.kwargs["content"]
    assert DRIVE_FILENAME.encode() in body


def test_save_binary_with_token_returns_none_on_401(drive_tool_personal):
    """Returns None when the server responds 401."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = drive_tool_personal._save_binary_with_token(
            token="bad-token",
            filename=DRIVE_FILENAME,
            raw_bytes=PDF_BYTES,
            mime_type="application/pdf",
            folder_id="",
        )

    assert result is None


def test_save_binary_with_token_returns_none_on_403(drive_tool_personal):
    """Returns None when the server responds 403."""
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.__enter__.return_value.post.return_value = mock_resp
        result = drive_tool_personal._save_binary_with_token(
            token="no-permission-token",
            filename=DRIVE_FILENAME,
            raw_bytes=PDF_BYTES,
            mime_type="application/pdf",
            folder_id="",
        )

    assert result is None


def test_save_binary_with_token_includes_folder_id_in_metadata(drive_tool_personal):
    """When folder_id is provided it appears in the JSON metadata body."""
    folder_id = "folder-xyz-123"
    mock_resp = _make_drive_response()

    with patch("httpx.Client") as mock_httpx:
        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp

        drive_tool_personal._save_binary_with_token(
            token=SYS_TOKEN,
            filename=DRIVE_FILENAME,
            raw_bytes=PDF_BYTES,
            mime_type="application/pdf",
            folder_id=folder_id,
        )

    body = mock_client.post.call_args.kwargs["content"].decode("utf-8", errors="replace")
    # The JSON metadata part should reference the folder as a parent
    assert folder_id in body
