"""Unit tests for SheetsTool record-oriented helpers."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.schoopet.drive_sheets_tool import SheetsTool


def _make_sheets_service(value_responses: list[dict] | None = None) -> MagicMock:
    service = MagicMock()
    values_api = service.spreadsheets.return_value.values.return_value

    responses = iter(value_responses or [])

    def get_execute():
        return next(responses)

    values_api.get.return_value.execute.side_effect = get_execute
    values_api.append.return_value.execute.return_value = {
        "updates": {"updatedCells": 3}
    }
    values_api.update.return_value.execute.return_value = {}
    service.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "sheet123",
        "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet123/edit",
    }
    service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {}
    return service


@pytest.fixture
def sheets_tool():
    tool = SheetsTool.__new__(SheetsTool)
    tool._oauth_client = MagicMock()
    return tool


def test_sheet_metadata_with_token(sheets_tool):
    service = _make_sheets_service(
        [
            {
                "values": [
                    ["Name", "Email"],
                    ["Ada", "ada@example.com"],
                    ["Linus", "linus@example.com"],
                ]
            }
        ]
    )

    metadata = sheets_tool._sheet_metadata_with_token(service, "sheet123", "Leads")

    assert metadata == {
        "sheet_id": "sheet123",
        "sheet_tab": "Leads",
        "headers": ["Name", "Email"],
        "header_count": 2,
        "row_count": 3,
        "data_row_count": 2,
    }


def test_read_records_with_token(sheets_tool):
    service = _make_sheets_service(
        [
            {
                "values": [
                    ["Name", "Email", "Role"],
                    ["Ada", "ada@example.com", "Engineer"],
                    ["Linus", "linus@example.com"],
                ]
            }
        ]
    )

    result = sheets_tool._read_records_with_token(service, "sheet123", "Leads", max_rows=10)

    assert result["headers"] == ["Name", "Email", "Role"]
    assert result["records"] == [
        {"Name": "Ada", "Email": "ada@example.com", "Role": "Engineer"},
        {"Name": "Linus", "Email": "linus@example.com", "Role": ""},
    ]


def test_ensure_headers_with_token_adds_missing_headers(sheets_tool):
    service = _make_sheets_service([{"values": [["Name"]]}])
    sheets_tool._add_column_with_token = MagicMock(
        side_effect=[
            "Column 'Email' added at B1.",
            "Column 'Role' added at C1.",
        ]
    )

    result = sheets_tool._ensure_headers_with_token(
        service,
        "sheet123",
        ["Name", "Email", "Role"],
        "Leads",
    )

    assert result["headers"] == ["Name", "Email", "Role"]
    assert result["added_headers"] == ["Email", "Role"]


def test_append_record_with_token_uses_header_order(sheets_tool):
    service = _make_sheets_service(
        [
            {"values": [["Email", "Name"]]},
            {"values": [["Email", "Name"]]},
        ]
    )
    sheets_tool._ensure_headers_with_token = MagicMock(
        return_value={"headers": ["Email", "Name"], "added_headers": []}
    )
    sheets_tool._append_row_with_token = MagicMock(return_value="Row appended to sheet (2 cells updated).")

    result = sheets_tool._append_record_with_token(
        service,
        "sheet123",
        {"Name": "Ada", "Email": "ada@example.com"},
        "Leads",
    )

    sheets_tool._append_row_with_token.assert_called_once_with(
        service,
        ["ada@example.com", "Ada"],
        "sheet123",
        "Leads",
    )
    assert result["record"] == {"Email": "ada@example.com", "Name": "Ada"}


def test_find_rows_with_token_returns_matches(sheets_tool):
    service = _make_sheets_service(
        [
            {
                "values": [
                    ["Name", "Email"],
                    ["Ada", "ada@example.com"],
                    ["Linus", "linus@example.com"],
                    ["Ada", "ada2@example.com"],
                ]
            }
        ]
    )

    result = sheets_tool._find_rows_with_token(
        service,
        "sheet123",
        "Name",
        "Ada",
        "Leads",
        max_rows=10,
    )

    assert result["returned_matches"] == 2
    assert result["matches"][0]["row_number"] == 2
    assert result["matches"][1]["record"]["Email"] == "ada2@example.com"


def test_find_rows_with_token_unknown_column_raises(sheets_tool):
    service = _make_sheets_service([{"values": [["Name", "Email"]]}])

    with pytest.raises(ValueError, match="Column 'Company' not found"):
        sheets_tool._find_rows_with_token(
            service,
            "sheet123",
            "Company",
            "OpenAI",
            "Leads",
            max_rows=10,
        )


def test_update_row_with_token_updates_by_header(sheets_tool):
    service = _make_sheets_service([{"values": [["Name", "Email", "Role"]]}])
    sheets_tool._update_cell_with_token = MagicMock()

    result = sheets_tool._update_row_with_token(
        service,
        "sheet123",
        4,
        {"Email": "ada@example.com", "Role": "Engineer"},
        "Leads",
    )

    assert result["row"] == 4
    sheets_tool._update_cell_with_token.assert_any_call(
        service,
        "sheet123",
        4,
        2,
        "ada@example.com",
        "Leads",
    )
    sheets_tool._update_cell_with_token.assert_any_call(
        service,
        "sheet123",
        4,
        3,
        "Engineer",
        "Leads",
    )


@pytest.mark.asyncio
async def test_public_read_sheet_records_returns_json(sheets_tool):
    sheets_tool._get_service = AsyncMock(return_value=(MagicMock(), None))
    sheets_tool._read_records_with_token = MagicMock(
        return_value={
            "sheet_id": "sheet123",
            "sheet_tab": "Leads",
            "headers": ["Name"],
            "records": [{"Name": "Ada"}],
            "returned_records": 1,
            "remaining_records": 0,
        }
    )
    tool_context = MagicMock()
    tool_context.user_id = "+15555550123"

    result = await sheets_tool.read_sheet_records("sheet123", "Leads", 10, tool_context)

    parsed = json.loads(result)
    assert parsed["records"] == [{"Name": "Ada"}]


def test_create_spreadsheet_with_headers(sheets_tool):
    service = _make_sheets_service()
    sheets_tool._ensure_headers_with_token = MagicMock(
        return_value={"headers": ["Name", "Email"], "added_headers": ["Name", "Email"]}
    )

    result = sheets_tool._create_spreadsheet_with_token(
        service,
        "Hiring Tracker",
        "Candidates",
        ["Name", "Email"],
    )

    create_call = service.spreadsheets.return_value.create.call_args.kwargs
    assert create_call["body"]["properties"]["title"] == "Hiring Tracker"
    assert create_call["body"]["sheets"][0]["properties"]["title"] == "Candidates"
    sheets_tool._ensure_headers_with_token.assert_called_once_with(
        service,
        "sheet123",
        ["Name", "Email"],
        "Candidates",
    )
    assert result["spreadsheet_id"] == "sheet123"
    assert result["sheet_tab"] == "Candidates"


def test_add_sheet_tab_with_headers(sheets_tool):
    service = _make_sheets_service()
    sheets_tool._ensure_headers_with_token = MagicMock(
        return_value={"headers": ["Name"], "added_headers": ["Name"]}
    )

    result = sheets_tool._add_sheet_tab_with_token(
        service,
        "sheet123",
        "Archive",
        ["Name"],
    )

    batch_call = service.spreadsheets.return_value.batchUpdate.call_args.kwargs
    assert batch_call["spreadsheetId"] == "sheet123"
    assert batch_call["body"]["requests"][0]["addSheet"]["properties"]["title"] == "Archive"
    sheets_tool._ensure_headers_with_token.assert_called_once_with(
        service,
        "sheet123",
        ["Name"],
        "Archive",
    )
    assert result == {
        "spreadsheet_id": "sheet123",
        "sheet_tab": "Archive",
        "headers": ["Name"],
    }


@pytest.mark.asyncio
async def test_public_create_spreadsheet_returns_json(sheets_tool):
    sheets_tool._get_service = AsyncMock(return_value=(MagicMock(), None))
    sheets_tool._create_spreadsheet_with_token = MagicMock(
        return_value={
            "spreadsheet_id": "sheet123",
            "spreadsheet_url": "https://docs.google.com/spreadsheets/d/sheet123/edit",
            "title": "Hiring Tracker",
            "sheet_tab": "Candidates",
            "headers": ["Name"],
        }
    )
    tool_context = MagicMock()
    tool_context.user_id = "+15555550123"

    result = await sheets_tool.create_spreadsheet(
        "Hiring Tracker",
        "Candidates",
        ["Name"],
        tool_context,
    )

    parsed = json.loads(result)
    assert parsed["spreadsheet_id"] == "sheet123"
    assert parsed["sheet_tab"] == "Candidates"
