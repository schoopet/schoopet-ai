"""Unit tests for DocsTool helpers."""

import json
from unittest.mock import MagicMock

from agents.schoopet.drive_sheets_tool import DocsTool


def _make_docs_service(
    *,
    document_response: dict | None = None,
    batch_response: dict | None = None,
) -> MagicMock:
    service = MagicMock()
    service.documents.return_value.get.return_value.execute.return_value = document_response or {
        "title": "Project Plan",
        "body": {
            "content": [
                {"endIndex": 1},
                {
                    "endIndex": 13,
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Hello world\n"}}
                        ]
                    },
                },
            ]
        },
    }
    service.documents.return_value.batchUpdate.return_value.execute.return_value = batch_response or {
        "replies": [{"replaceAllText": {"occurrencesChanged": 2}}]
    }
    return service


def _make_drive_service() -> MagicMock:
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {
        "id": "doc123",
        "webViewLink": "https://docs.google.com/document/d/doc123/edit",
    }
    return service


def test_create_google_doc_with_content():
    tool = DocsTool.__new__(DocsTool)
    docs_service = _make_docs_service()
    drive_service = _make_drive_service()
    tool._append_to_google_doc_with_token = MagicMock(
        return_value={"document_id": "doc123", "appended_characters": 5}
    )

    result = tool._create_google_doc_with_token(
        docs_service,
        drive_service,
        "Project Plan",
        "Hello",
        "folder123",
    )

    create_call = drive_service.files.return_value.create.call_args.kwargs
    assert create_call["body"] == {
        "name": "Project Plan",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["folder123"],
    }
    tool._append_to_google_doc_with_token.assert_called_once_with(
        docs_service,
        "doc123",
        "Hello",
    )
    assert result["document_id"] == "doc123"


def test_read_google_doc_with_token():
    tool = DocsTool.__new__(DocsTool)
    docs_service = _make_docs_service()

    result = tool._read_google_doc_with_token(docs_service, "doc123")

    assert result == {
        "document_id": "doc123",
        "title": "Project Plan",
        "text": "Hello world\n",
    }


def test_append_to_google_doc_with_token():
    tool = DocsTool.__new__(DocsTool)
    docs_service = _make_docs_service()

    result = tool._append_to_google_doc_with_token(docs_service, "doc123", "More text")

    batch_call = docs_service.documents.return_value.batchUpdate.call_args.kwargs
    request = batch_call["body"]["requests"][0]["insertText"]
    assert batch_call["documentId"] == "doc123"
    assert request["location"]["index"] == 12
    assert request["text"] == "More text"
    assert result == {"document_id": "doc123", "appended_characters": 9}


def test_replace_text_in_google_doc_with_token():
    tool = DocsTool.__new__(DocsTool)
    docs_service = _make_docs_service()

    result = tool._replace_text_in_google_doc_with_token(
        docs_service,
        "doc123",
        "old",
        "new",
    )

    batch_call = docs_service.documents.return_value.batchUpdate.call_args.kwargs
    request = batch_call["body"]["requests"][0]["replaceAllText"]
    assert request["containsText"]["text"] == "old"
    assert request["replaceText"] == "new"
    assert result["occurrences_changed"] == 2


def test_public_read_google_doc_returns_json():
    tool = DocsTool.__new__(DocsTool)
    tool._oauth_client = MagicMock()
    tool._get_services = MagicMock(return_value=(MagicMock(), MagicMock(), None))
    tool._read_google_doc_with_token = MagicMock(
        return_value={
            "document_id": "doc123",
            "title": "Project Plan",
            "text": "Hello world\n",
        }
    )
    tool_context = MagicMock()
    tool_context.user_id = "+15555550123"

    result = tool.read_google_doc("doc123", tool_context)

    parsed = json.loads(result)
    assert parsed["document_id"] == "doc123"
    assert parsed["title"] == "Project Plan"
