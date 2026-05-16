"""Unit tests for EmailTool."""
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from google.adk.artifacts import InMemoryArtifactService

from agents.schoopet.email_tool import EmailTool

PHONE = "+19494136310"
SESSION_ID = "sess-test"
MSG_ID = "msg001"
PDF_BYTES = b"%PDF-1.4 fake pdf content"
PDF_B64 = base64.urlsafe_b64encode(PDF_BYTES).decode()
BODY_TEXT = "Please find the resume attached."
BODY_B64 = base64.urlsafe_b64encode(BODY_TEXT.encode()).decode()


def _make_full_msg(attachments_payload: list[dict]) -> dict:
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
            "parts": [{"mimeType": "text/plain", "body": {"data": BODY_B64}}, *attachments_payload],
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


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeAttachmentsResource:
    def __init__(self, attachment_payloads=None):
        self.attachment_payloads = attachment_payloads or {}
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.attachment_payloads.get(kwargs["id"], {})
        return _FakeRequest(payload)


class _FakeMessagesResource:
    def __init__(self, list_payload=None, message_payloads=None, attachment_payloads=None):
        self.list_payload = list_payload or {"messages": []}
        self.message_payloads = message_payloads or {}
        self.list_calls = []
        self.get_calls = []
        self._attachments = _FakeAttachmentsResource(attachment_payloads)

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeRequest(self.list_payload)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        payload = self.message_payloads[(kwargs["id"], kwargs["format"])]
        return _FakeRequest(payload)

    def attachments(self):
        return self._attachments


class _FakeUsersResource:
    def __init__(self, messages_resource):
        self._messages = messages_resource

    def messages(self):
        return self._messages


class _FakeGmailService:
    def __init__(self, messages_resource):
        self._users = _FakeUsersResource(messages_resource)

    def users(self):
        return self._users


async def _make_artifact_service_with_save(user_id: str = PHONE):
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


def _make_email_tool(service=None) -> EmailTool:
    tool = EmailTool()
    tool._firestore_client = None
    tool._get_gmail_service = AsyncMock(return_value=service)
    return tool


@pytest.mark.asyncio
async def test_fetch_email_stores_pdf_artifact():
    svc, ctx = await _make_artifact_service_with_save()
    full_msg = _make_full_msg([_PDF_PART])
    messages = _FakeMessagesResource(
        message_payloads={(MSG_ID, "full"): full_msg},
    )
    tool = _make_email_tool(_FakeGmailService(messages))

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
    _, ctx = await _make_artifact_service_with_save()
    full_msg = _make_full_msg([_PDF_PART])
    messages = _FakeMessagesResource(
        message_payloads={(MSG_ID, "full"): full_msg},
    )
    tool = _make_email_tool(_FakeGmailService(messages))

    result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    assert f'artifact: "{MSG_ID}_resume.pdf"' in result
    assert "application/pdf" in result
    assert "save_attachment_to_drive" in result
    assert "boss@company.com" in result
    assert "Resume" in result


@pytest.mark.asyncio
async def test_fetch_email_skips_unsupported_types():
    svc, ctx = await _make_artifact_service_with_save()
    full_msg = _make_full_msg([_ZIP_PART])
    messages = _FakeMessagesResource(
        message_payloads={(MSG_ID, "full"): full_msg},
    )
    tool = _make_email_tool(_FakeGmailService(messages))

    result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    stored = await svc.load_artifact(
        app_name="coordinator",
        user_id=PHONE,
        session_id=SESSION_ID,
        filename=f"{MSG_ID}_archive.zip",
    )
    assert stored is None
    assert "archive.zip" in result
    assert "not supported" in result
    assert f'artifact: "{MSG_ID}_archive.zip"' not in result


@pytest.mark.asyncio
async def test_fetch_email_no_tool_context():
    full_msg = _make_full_msg([_PDF_PART])
    messages = _FakeMessagesResource(
        message_payloads={(MSG_ID, "full"): full_msg},
    )
    tool = _make_email_tool(_FakeGmailService(messages))

    result = await tool.fetch_email(message_id=MSG_ID, tool_context=None)

    assert "no user_id" in result.lower()


@pytest.mark.asyncio
async def test_fetch_email_artifact_service_failure():
    _, ctx = await _make_artifact_service_with_save()

    async def failing_save(filename, artifact):
        raise Exception("storage failure")

    ctx.save_artifact = failing_save
    full_msg = _make_full_msg([_PDF_PART])
    messages = _FakeMessagesResource(
        message_payloads={(MSG_ID, "full"): full_msg},
    )
    tool = _make_email_tool(_FakeGmailService(messages))

    result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    assert "boss@company.com" in result
    assert "artifact:" not in result


@pytest.mark.asyncio
async def test_fetch_email_no_user_token():
    # When the IAM connector has no credential it emits a request and returns None;
    # fetch_email should return "" (credential request sent, nothing to display).
    _, ctx = await _make_artifact_service_with_save()
    tool = _make_email_tool(None)

    result = await tool.fetch_email(message_id=MSG_ID, tool_context=ctx)

    assert result == ""
