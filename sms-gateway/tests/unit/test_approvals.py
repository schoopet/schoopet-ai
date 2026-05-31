"""Unit tests for PendingApprovalCoordinator.format_notification."""
import pytest
from src.session.approvals import PendingApprovalCoordinator


def _pending(tool_name: str, sheet_id: str = "sheet123", row: int = 2) -> dict:
    return {
        "id": f"pending_{row}",
        "tool_name": tool_name,
        "tool_args": {"sheet_id": sheet_id, "row": row, "updates": {"Name": "Ada"}},
        "approval_resource_id": sheet_id,
        "approval_resource_label": "Google Sheet",
        "approval_notification_id": "pending_2",
        "is_group_notification_owner": row == 2,
    }


@pytest.fixture
def coord():
    return PendingApprovalCoordinator()


def test_single_action_shows_full_args(coord):
    msg = coord.format_notification([_pending("update_sheet_row")])
    assert "Approve this action for this Google Sheet?" in msg
    assert "sheet123" in msg
    assert "update_sheet_row" in msg


def test_small_group_lists_each_action(coord):
    group = [_pending("update_sheet_row", row=i) for i in range(2, 5)]
    msg = coord.format_notification(group)
    assert "Approve all 3 actions?" in msg
    assert msg.count("- update_sheet_row") == 3


def test_large_uniform_group_collapses_to_summary(coord):
    group = [_pending("update_sheet_row", row=i) for i in range(2, 34)]  # 32 rows
    msg = coord.format_notification(group)
    assert "Approve all 32 actions?" in msg
    assert "32 × update_sheet_row" in msg
    # must not list each row individually — stays well under Discord's 4000-char limit
    assert len(msg) < 300


def test_large_mixed_group_truncates_with_overflow_line(coord):
    group = (
        [_pending("update_sheet_row", row=i) for i in range(2, 6)]
        + [_pending("append_record_to_sheet", row=i) for i in range(6, 10)]
    )
    msg = coord.format_notification(group)
    assert "Approve all 8 actions?" in msg
    assert "… and 3 more" in msg
    assert msg.count("\n- ") == PendingApprovalCoordinator.MAX_LISTED_ACTIONS + 1  # 5 listed + overflow


def test_no_resource_id_uses_generic_header(coord):
    pending = {
        "id": "p1",
        "tool_name": "create_spreadsheet",
        "tool_args": {},
        "approval_resource_id": "",
        "approval_resource_label": "",
        "approval_notification_id": "p1",
        "is_group_notification_owner": True,
    }
    msg = coord.format_notification([pending])
    assert msg.startswith("Approve this action?")
