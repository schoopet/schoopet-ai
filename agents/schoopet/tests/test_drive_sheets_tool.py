"""Unit tests for DriveTool upload and list behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

from agents.schoopet.drive_sheets_tool import DriveTool

PHONE = "+19494136310"
SESSION_ID = "sess-test"
ARTIFACT_FILENAME = "msg001_resume.pdf"
DRIVE_FILENAME = "resume_john.pdf"
PDF_BYTES = b"%PDF-1.4 content"
DRIVE_FILE_ID = "drive-file-abc"


def _make_drive_service(
    *,
    create_response: dict | None = None,
    list_response: dict | None = None,
) -> MagicMock:
    service = MagicMock()
    files_api = service.files.return_value
    files_api.create.return_value.execute.return_value = create_response or {"id": DRIVE_FILE_ID}
    files_api.list.return_value.execute.return_value = list_response or {"files": []}
    return service


async def _make_artifact_service(
    filename: str = ARTIFACT_FILENAME,
    data: bytes = PDF_BYTES,
    mime_type: str = "application/pdf",
) -> InMemoryArtifactService:
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


@pytest.fixture
def drive_tool():
    tool = DriveTool.__new__(DriveTool)
    tool._oauth_client = MagicMock()
    return tool


@pytest.mark.asyncio
async def test_save_attachment_uses_service_for_user_upload(drive_tool):
    svc = await _make_artifact_service()
    ctx = _make_tool_context(svc)
    drive_service = _make_drive_service()
    drive_tool._get_service = AsyncMock(return_value=drive_service)

    result = await drive_tool.save_attachment_to_drive(
        artifact_filename=ARTIFACT_FILENAME,
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert "Saved" in result
    assert DRIVE_FILENAME in result
    assert DRIVE_FILE_ID in result
    drive_tool._get_service.assert_called_once_with(ctx)

    create_call = drive_service.files.return_value.create.call_args.kwargs
    assert create_call["body"] == {"name": DRIVE_FILENAME, "mimeType": "application/pdf"}
    assert create_call["fields"] == "id"
    media = create_call["media_body"]
    assert media.mimetype() == "application/pdf"
    assert media.size() == len(PDF_BYTES)
    assert media.getbytes(0, len(PDF_BYTES)) == PDF_BYTES


@pytest.mark.asyncio
async def test_save_attachment_artifact_not_found(drive_tool):
    svc = InMemoryArtifactService()
    ctx = _make_tool_context(svc)

    result = await drive_tool.save_attachment_to_drive(
        artifact_filename="missing.pdf",
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert "ERROR" in result
    assert "missing.pdf" in result


@pytest.mark.asyncio
async def test_save_attachment_no_user_id(drive_tool):
    ctx = AsyncMock()
    ctx.user_id = None

    result = await drive_tool.save_attachment_to_drive(
        artifact_filename=ARTIFACT_FILENAME,
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert "ERROR" in result
    assert "user_id" in result


@pytest.mark.asyncio
async def test_save_attachment_no_token_returns_auth_instructions(drive_tool):
    svc = await _make_artifact_service()
    ctx = _make_tool_context(svc)
    drive_tool._get_service = AsyncMock(return_value=None)

    result = await drive_tool.save_attachment_to_drive(
        artifact_filename=ARTIFACT_FILENAME,
        drive_filename=DRIVE_FILENAME,
        tool_context=ctx,
    )

    assert result == ""


def test_save_binary_with_token_builds_media_upload(drive_tool):
    drive_service = _make_drive_service()

    result = drive_tool._save_binary_with_token(
        service=drive_service,
        filename=DRIVE_FILENAME,
        raw_bytes=PDF_BYTES,
        mime_type="application/pdf",
        folder_id="",
    )

    assert DRIVE_FILENAME in result
    create_call = drive_service.files.return_value.create.call_args.kwargs
    assert create_call["body"] == {"name": DRIVE_FILENAME, "mimeType": "application/pdf"}
    media = create_call["media_body"]
    assert media.mimetype() == "application/pdf"
    assert media.size() == len(PDF_BYTES)
    assert media.getbytes(0, len(PDF_BYTES)) == PDF_BYTES


def test_save_binary_with_token_includes_folder_id_in_metadata(drive_tool):
    folder_id = "folder-xyz-123"
    drive_service = _make_drive_service()

    drive_tool._save_binary_with_token(
        service=drive_service,
        filename=DRIVE_FILENAME,
        raw_bytes=PDF_BYTES,
        mime_type="application/pdf",
        folder_id=folder_id,
    )

    create_call = drive_service.files.return_value.create.call_args.kwargs
    assert create_call["body"]["parents"] == [folder_id]


def test_list_files_formats_results(drive_tool):
    drive_service = _make_drive_service(
        list_response={
            "files": [
                {
                    "id": "file-1",
                    "name": "resume.pdf",
                    "mimeType": "application/pdf",
                    "modifiedTime": "2026-04-26T12:00:00Z",
                }
            ]
        }
    )

    result = drive_tool._list_files_with_token(
        service=drive_service,
        folder_id="folder-123",
        query="name contains 'resume'",
        max_results=10,
    )

    assert "Files in folder (1):" in result
    assert "resume.pdf | ID: file-1 | PDF | Modified: 2026-04-26" in result

    list_call = drive_service.files.return_value.list.call_args.kwargs
    assert list_call["q"] == "'folder-123' in parents and trashed=false and name contains 'resume'"
    assert list_call["pageSize"] == 10


def test_list_files_returns_empty_message(drive_tool):
    drive_service = _make_drive_service(list_response={"files": []})

    result = drive_tool._list_files_with_token(
        service=drive_service,
        folder_id="folder-123",
        query="",
        max_results=10,
    )

    assert result == "No files found in that folder."
