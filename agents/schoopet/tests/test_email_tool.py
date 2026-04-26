"""Unit tests for EmailTool.fetch_email.

Strategy:
- Wire tool_context.save_artifact to an InMemoryArtifactService so we can
  verify stored artifacts without GCS credentials.
- Mock httpx.Client to simulate Gmail API responses.
- Mock _oauth_client.get_valid_access_token to avoid live OAuth calls.
"""
import base64

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

from agents.schoopet.email_tool import EmailTool

PHONE = "+19494136310"
SESSION_ID = "sess-test"
MSG_ID = "msg001"
PDF_BYTES = b"%PDF-1.4 fake pdf content"
PDF_B64 = base64.urlsafe_b64encode(PDF_BYTES).decode()
BODY_TEXT = "Please find the resume attached."
BODY_B64 = base64.urlsafe_b64encode(BODY_TEXT.encode()).decode()
SYSTEM_TOKEN = "sys-access-token"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_full_msg(attachments_payload: list[dict]) -> dict:
    """Build a minimal Gmail API 'full' message dict."""
    return {
        "id": MSG_ID,
        "snippet": BODY_TEXT,
        "payload": {
            "headers": [
                {"name": "From", "value": "boss@company.com"},
                {"name": "Subject", "value": "Resume"},
                {"name": "Date", "value": "Mon, 01 Jan 2025 10:00:00 +0000"},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": BODY_B64},
                },
                *attachments_payload,
            ],
        },
    }


_PDF_PART = {
    "mimeType": "application/pdf",
    "filename": "resume.pdf",
    "body": {"data": PDF_B64},
}

_ZIP_PART = {
    "mimeType": "application/zip",
    "filename": "archive.zip",
    "body": {"data": base64.urlsafe_b64encode(b"PK fake zip").decode()},
}


async def _make_artifact_service_with_save(
    user_id: str = PHONE,
) -> tuple[InMemoryArtifactService, AsyncMock]:
    """Return (service, tool_context) with save_artifact wired to the service."""
    svc = InMemoryArtifactService()
    ctx = AsyncMock()
    ctx.user_id = user_id

    async def save_artifact(filename, artifact):
        await svc.save_artifact(
            app_name="coordinator",
            user_id=user_id,
            session_id=SESSION_ID,
            filename=filename,
            artifact=artifact,
        )

    async def load_artifact(filename, version=None):
        return await svc.load_artifact(
            app_name="coordinator",
            user_id=user_id,
            session_id=SESSION_ID,
            filename=filename,
            version=version,
        )

    ctx.save_artifact = save_artifact
    ctx.load_artifact = load_artifact
    return svc, ctx


def _make_email_tool(token: str = SYSTEM_TOKEN) -> EmailTool:
    tool = EmailTool("team")
    mock_oauth = MagicMock()
    mock_oauth.get_valid_access_token.return_value = token
    tool._oauth_client = mock_oauth
    tool._firestore_client = None
    return tool


def _mock_httpx_get(json_response: dict):
    """Return a context manager mock for httpx.Client whose get() returns json_response."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_response

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp

    mock_cls = MagicMock()
    mock_cls.return_value.__enter__.return_value = mock_client
    mock_cls.return_value.__exit__.return_value = False
    return mock_cls


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_email_stores_pdf_artifact():
    """Artifact is stored at {msg_id}_resume.pdf with correct bytes and MIME type."""
    svc, ctx = await _make_artifact_service_with_save()
    tool = _make_email_tool()
    full_msg = _make_full_msg([_PDF_PART])

    with patch("httpx.Client", _mock_httpx_get(full_msg)):
        await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    stored = await svc.load_artifact(
        app_name="coordinator",
        user_id=PHONE,
        session_id=SESSION_ID,
        filename=f"{MSG_ID}_resume.pdf",
    )
    assert stored is not None
    assert stored.inline_data.mime_type == "application/pdf"
    assert stored.inline_data.data == PDF_BYTES


@pytest.mark.asyncio
async def test_fetch_email_returns_artifact_section():
    """Return string contains artifact key and save_attachment_to_drive."""
    svc, ctx = await _make_artifact_service_with_save()
    tool = _make_email_tool()
    full_msg = _make_full_msg([_PDF_PART])

    with patch("httpx.Client", _mock_httpx_get(full_msg)):
        result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    assert f'artifact: "{MSG_ID}_resume.pdf"' in result
    assert "application/pdf" in result
    assert "save_attachment_to_drive" in result
    # Email headers also present
    assert "boss@company.com" in result
    assert "Resume" in result


@pytest.mark.asyncio
async def test_fetch_email_skips_unsupported_types():
    """ZIP attachment is not stored; appears as unsupported note."""
    svc, ctx = await _make_artifact_service_with_save()
    tool = _make_email_tool()
    full_msg = _make_full_msg([_ZIP_PART])

    with patch("httpx.Client", _mock_httpx_get(full_msg)):
        result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    # Nothing stored for ZIP
    stored = await svc.load_artifact(
        app_name="coordinator",
        user_id=PHONE,
        session_id=SESSION_ID,
        filename=f"{MSG_ID}_archive.zip",
    )
    assert stored is None

    # ZIP shows as unsupported note
    assert "archive.zip" in result
    assert "not supported" in result
    # No artifact key for the ZIP
    assert f'artifact: "{MSG_ID}_archive.zip"' not in result


@pytest.mark.asyncio
async def test_fetch_email_no_tool_context():
    """tool_context=None still returns email text without crashing."""
    tool = _make_email_tool()
    full_msg = _make_full_msg([_PDF_PART])

    with patch("httpx.Client", _mock_httpx_get(full_msg)):
        result = await tool.fetch_email(message_id=MSG_ID, tool_context=None)

    # Email content present
    assert "boss@company.com" in result
    assert "Resume" in result
    # No artifact section (nothing was saved)
    assert "artifact:" not in result


@pytest.mark.asyncio
async def test_fetch_email_artifact_service_failure():
    """save_artifact raising still returns email text (warning logged)."""
    svc, ctx = await _make_artifact_service_with_save()

    # Override save_artifact to raise
    async def failing_save(filename, artifact):
        raise Exception("storage failure")

    ctx.save_artifact = failing_save

    tool = _make_email_tool()
    full_msg = _make_full_msg([_PDF_PART])

    with patch("httpx.Client", _mock_httpx_get(full_msg)):
        result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    # Email text still returned
    assert "boss@company.com" in result
    # No artifact section (save failed)
    assert "artifact:" not in result


@pytest.mark.asyncio
async def test_fetch_email_no_system_token():
    """Returns 'not connected' error when system token is unavailable."""
    tool = _make_email_tool(token=None)

    result = await tool.fetch_email(message_id=MSG_ID, tool_context=None)

    assert "not connected" in result.lower()
