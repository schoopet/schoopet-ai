"""Interactive pending approval grouping and notification formatting.

This module is only for live approvals that wait for a user click, such as
Discord button prompts. Offline/background tasks use allowed_resource_ids and
seeded ADK session state instead; they do not create pending approvals here.
"""
import json
from datetime import datetime
from typing import Any


MAX_APPROVAL_ARG_CHARS = 700
RESOURCE_ID_ARGS = {
    "sheet_id": "Google Sheet",
    "document_id": "Google Doc",
    "folder_id": "Drive folder",
}


class PendingApprovalCoordinator:
    """Build and group live pending ADK approvals.

    Google Workspace resources use one approval group per resource ID, regardless
    of whether a tool calls that value sheet_id, document_id, or folder_id.
    Confirmations without a resource ID fall back to one approval per action.
    This is the same resource-ID scope used by offline allowed_resource_ids, but
    the storage and lifecycle here are only for interactive user notifications.
    """

    def build_pending(
        self,
        *,
        confirmation_data: dict[str, Any],
        existing_pending: list[dict[str, Any]],
        pending_id: str,
        user_id: str,
        session_id: str,
        channel: str,
        session_scope: str,
        created_at: datetime,
    ) -> dict[str, Any]:
        group_id, resource_id, resource_label = self._group_for_confirmation(
            confirmation_data,
            pending_id,
        )
        existing_owner_id = self._existing_notification_id(existing_pending, group_id)

        return {
            "id": pending_id,
            "user_id": user_id,
            "agent_session_id": session_id,
            "adk_confirmation_function_call_id": confirmation_data.get("function_call_id", ""),
            "original_function_call": confirmation_data.get("original_function_call", {}),
            "original_function_call_id": confirmation_data.get("original_function_call_id", ""),
            "tool_name": confirmation_data.get("tool_name", "unknown_tool"),
            "tool_args": confirmation_data.get("tool_args", {}),
            "hint": confirmation_data.get("hint", ""),
            "payload": confirmation_data.get("payload"),
            "created_at": created_at,
            "channel": channel,
            "session_scope": session_scope,
            "approval_group_id": group_id,
            "approval_resource_id": resource_id,
            "approval_resource_label": resource_label,
            "approval_notification_id": existing_owner_id or pending_id,
            "is_group_notification_owner": not bool(existing_owner_id),
        }

    def group_for_pending_id(
        self,
        pending_approvals: list[dict[str, Any]],
        pending_id: str,
    ) -> list[dict[str, Any]]:
        anchor = self._find_pending(pending_approvals, pending_id)
        if not anchor:
            return []

        group_id = anchor.get("approval_group_id") or anchor.get("id")
        return [
            pending for pending in pending_approvals
            if (pending.get("approval_group_id") or pending.get("id")) == group_id
        ]

    def clear_group_for_pending_id(
        self,
        pending_approvals: list[dict[str, Any]],
        pending_id: str,
    ) -> list[dict[str, Any]]:
        anchor = self._find_pending(pending_approvals, pending_id)
        if not anchor:
            return pending_approvals

        group_id = anchor.get("approval_group_id") or anchor.get("id")
        return [
            pending for pending in pending_approvals
            if (pending.get("approval_group_id") or pending.get("id")) != group_id
        ]

    def notification_id(self, pending_group: list[dict[str, Any]]) -> str:
        if not pending_group:
            return ""
        return (
            pending_group[0].get("approval_notification_id")
            or pending_group[0].get("id")
            or ""
        )

    def should_send_notification(self, pending_group: list[dict[str, Any]]) -> bool:
        return any(
            pending.get("is_group_notification_owner", True)
            for pending in pending_group
        )

    def format_notification(self, pending_group: list[dict[str, Any]]) -> str:
        first = pending_group[0]
        resource_id = first.get("approval_resource_id") or ""
        resource_label = first.get("approval_resource_label") or "resource"

        if resource_id:
            header = f"Approve {len(pending_group)} action(s) for this {resource_label}?\n{resource_id}"
        else:
            header = "Approve this action?"

        if len(pending_group) == 1:
            return f"{header}\n{self._format_action(first)}"

        actions = "\n".join(
            f"- {self._format_action(pending)}"
            for pending in pending_group
        )
        return f"{header}\n{actions}"

    def _group_for_confirmation(
        self,
        confirmation_data: dict[str, Any],
        pending_id: str,
    ) -> tuple[str, str, str]:
        tool_args = confirmation_data.get("tool_args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        for arg_name, label in RESOURCE_ID_ARGS.items():
            resource_id = str(tool_args.get(arg_name) or "").strip()
            if resource_id:
                return f"resource:{resource_id}", resource_id, label

        function_call_id = str(confirmation_data.get("function_call_id") or pending_id)
        return f"action:{function_call_id}", "", ""

    def _existing_notification_id(
        self,
        pending_approvals: list[dict[str, Any]],
        group_id: str,
    ) -> str:
        for pending in pending_approvals:
            if pending.get("approval_group_id") == group_id:
                return pending.get("approval_notification_id") or pending.get("id") or ""
        return ""

    @staticmethod
    def _find_pending(
        pending_approvals: list[dict[str, Any]],
        pending_id: str,
    ) -> dict[str, Any] | None:
        return next(
            (pending for pending in pending_approvals if pending.get("id") == pending_id),
            None,
        )

    @staticmethod
    def _summarize_args(args: dict[str, Any]) -> str:
        if not args:
            return ""
        try:
            text = json.dumps(args, sort_keys=True, default=str)
        except TypeError:
            text = str(args)
        if len(text) > MAX_APPROVAL_ARG_CHARS:
            return f"{text[:MAX_APPROVAL_ARG_CHARS - 3]}..."
        return text

    def _format_action(self, pending: dict[str, Any]) -> str:
        args = pending.get("tool_args") or {}
        if not isinstance(args, dict):
            args = {}
        return f"{pending.get('tool_name', 'unknown_tool')}({self._summarize_args(args)})"
