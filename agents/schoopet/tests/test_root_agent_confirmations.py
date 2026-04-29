"""Tests for native ADK confirmation policy on registered tools."""
import ast
from pathlib import Path


REQUIRE_CONFIRMATION = {
    "cancel_task",
    "approve_task",
    "request_correction",
    "create_calendar_event",
    "update_calendar_event",
    "save_file_to_drive",
    "save_attachment_to_drive",
    "create_google_doc",
    "append_to_google_doc",
    "replace_text_in_google_doc",
    "create_spreadsheet",
    "add_sheet_tab",
    "ensure_sheet_headers",
    "append_record_to_sheet",
    "update_sheet_row",
    "append_row_to_sheet",
    "add_sheet_column",
    "update_sheet_cell",
    "set_timezone",
    "add_email_rule",
    "update_email_rule",
    "remove_email_rule",
}

NO_CONFIRMATION = {
    "save_memory",
    "save_multiple_memories",
    "create_async_task",
    "check_task_status",
    "list_pending_tasks",
    "review_task_result",
    "list_calendar_events",
    "get_calendar_status",
    "list_drive_files",
    "get_drive_status",
    "read_google_doc",
    "get_docs_status",
    "get_sheet_schema",
    "read_sheet_records",
    "find_sheet_rows",
    "read_sheet",
    "get_sheets_status",
    "get_timezone",
    "get_current_time",
    "convert_time",
    "parse_natural_datetime",
    "next_occurrence",
    "get_cloud_task_status",
    "list_scheduled_tasks",
    "debug_task",
    "read_emails",
    "fetch_email",
    "list_artifacts",
    "read_artifact",
    "get_gmail_status",
    "list_email_rules",
}


def _function_tool_confirmations() -> dict[str, bool]:
    root_agent_path = Path(__file__).parents[1] / "root_agent.py"
    tree = ast.parse(root_agent_path.read_text())
    confirmations: dict[str, bool] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "FunctionTool":
            continue

        func_name = None
        require_confirmation = False
        for keyword in node.keywords:
            if keyword.arg == "func":
                if isinstance(keyword.value, ast.Attribute):
                    func_name = keyword.value.attr
                elif isinstance(keyword.value, ast.Name):
                    func_name = keyword.value.id
            if keyword.arg == "require_confirmation":
                require_confirmation = (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                )

        if func_name:
            confirmations[func_name] = require_confirmation

    return confirmations


def test_approved_mutating_tools_require_confirmation():
    confirmations = _function_tool_confirmations()

    missing = REQUIRE_CONFIRMATION - confirmations.keys()
    assert not missing
    assert {
        name for name in REQUIRE_CONFIRMATION if not confirmations[name]
    } == set()


def test_approved_non_mutating_tools_do_not_require_confirmation():
    confirmations = _function_tool_confirmations()

    missing = NO_CONFIRMATION - confirmations.keys()
    assert not missing
    assert {
        name for name in NO_CONFIRMATION if confirmations[name]
    } == set()
