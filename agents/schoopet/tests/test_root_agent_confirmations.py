"""Tests for native ADK confirmation policy on registered tools."""
import ast
from pathlib import Path


# Tools that keep require_confirmation=True (one-shot, non-repeatable)
REQUIRE_CONFIRMATION_BOOL = {
    "cancel_task",
    "create_calendar_event",
    "update_calendar_event",
    "delete_calendar_event",
    "save_file_to_drive",
    "create_google_doc",
    "create_spreadsheet",
    "set_timezone",
    "add_email_rule",
    "update_email_rule",
    "remove_email_rule",
}

# Tools that use make_resource_confirmation(...) for per-resource bulk approval
REQUIRE_CONFIRMATION_CALLABLE = {
    # Sheet tools — approve once per sheet_id
    "add_sheet_tab",
    "ensure_sheet_headers",
    "append_record_to_sheet",
    "update_sheet_row",
    "append_row_to_sheet",
    "add_sheet_column",
    "update_sheet_cell",
    # Doc tools — approve once per document_id
    "append_to_google_doc",
    "replace_text_in_google_doc",
    # Drive tools — approve once per folder_id
    "save_attachment_to_drive",
}

NO_CONFIRMATION = {
    "save_memory",
    "save_multiple_memories",
    "create_async_task",
    "check_task_status",
    "get_task_result",
    "list_pending_tasks",
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

# "bool" | "callable" | False
_ConfirmationKind = str | bool


def _function_tool_confirmations() -> dict[str, _ConfirmationKind]:
    root_agent_path = Path(__file__).parents[1] / "root_agent.py"
    tree = ast.parse(root_agent_path.read_text())
    confirmations: dict[str, _ConfirmationKind] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "FunctionTool":
            continue

        func_name = None
        kind: _ConfirmationKind = False
        for keyword in node.keywords:
            if keyword.arg == "func":
                if isinstance(keyword.value, ast.Attribute):
                    func_name = keyword.value.attr
                elif isinstance(keyword.value, ast.Name):
                    func_name = keyword.value.id
            if keyword.arg == "require_confirmation":
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    kind = "bool"
                elif isinstance(keyword.value, ast.Call):
                    kind = "callable"
                elif isinstance(keyword.value, ast.Name):
                    # variable holding a callable returned by make_resource_confirmation
                    kind = "callable"

        if func_name:
            confirmations[func_name] = kind

    return confirmations


def test_bool_confirmation_tools():
    confirmations = _function_tool_confirmations()
    missing = REQUIRE_CONFIRMATION_BOOL - confirmations.keys()
    assert not missing, f"Tools missing from root_agent.py: {missing}"
    wrong = {name for name in REQUIRE_CONFIRMATION_BOOL if confirmations[name] != "bool"}
    assert not wrong, f"Expected require_confirmation=True for: {wrong}"


def test_callable_confirmation_tools():
    confirmations = _function_tool_confirmations()
    missing = REQUIRE_CONFIRMATION_CALLABLE - confirmations.keys()
    assert not missing, f"Tools missing from root_agent.py: {missing}"
    wrong = {name for name in REQUIRE_CONFIRMATION_CALLABLE if confirmations[name] != "callable"}
    assert not wrong, f"Expected require_confirmation=make_resource_confirmation(...) for: {wrong}"


def test_non_mutating_tools_have_no_confirmation():
    confirmations = _function_tool_confirmations()
    missing = NO_CONFIRMATION - confirmations.keys()
    assert not missing, f"Tools missing from root_agent.py: {missing}"
    wrong = {name for name in NO_CONFIRMATION if confirmations[name]}
    assert not wrong, f"Expected no require_confirmation for: {wrong}"
