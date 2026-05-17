"""Callable factories for per-resource bulk confirmation.

Passed as ``require_confirmation=make_resource_confirmation(...)`` on FunctionTool.
First call for a given resource ID requests a normal confirmation from the user.
After the user approves, that resource ID is stored in ADK session state and all
subsequent calls for the same resource skip the prompt automatically.

Pre-built singletons for the three standard resource types are exported at the
bottom of this module (sheet_confirmation, doc_confirmation, drive_folder_confirmation).
Prefer importing those over calling make_resource_confirmation directly.

The session-state key format is ``_RESOURCE_CONFIRMED_PREFIX + resource_id``.
The gateway task executor seeds these keys from the flat ``allowed_resource_ids`` list stored
on the task document, so offline pre-authorized IDs bypass the interactive
confirmation prompt. Live pending approval notifications are managed separately
by the SMS gateway session approval API.
"""
import logging
from typing import Any

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# Session-state key prefix shared with gateway async task execution.
_RESOURCE_CONFIRMED_PREFIX = "_resource_confirmed_"


def _approved_resource_ids(state_keys: list[str]) -> list[str]:
    """Extract resource IDs that are already approved in ADK session state."""
    return sorted(
        key[len(_RESOURCE_CONFIRMED_PREFIX):]
        for key in state_keys
        if key.startswith(_RESOURCE_CONFIRMED_PREFIX)
    )


def make_resource_confirmation(id_arg: str):
    """Return a require_confirmation callable scoped to a resource ID.

    Args:
        id_arg: Name of the function argument that carries the resource ID
                (e.g. "sheet_id", "document_id", "folder_id").
    """
    async def _check(tool_context: ToolContext = None, **kwargs: Any) -> bool:
        resource_id = kwargs.get(id_arg) or "_default_"
        state_key = f"{_RESOURCE_CONFIRMED_PREFIX}{resource_id}"

        if tool_context is None:
            logger.warning(
                "resource_confirmation: decision=require_confirmation reason=no_tool_context "
                "id_arg=%s actual_resource_id=%s approved_resource_ids=[]",
                id_arg,
                resource_id,
            )
            return True

        state_keys = list(tool_context.state.to_dict().keys())
        approved_resource_ids = _approved_resource_ids(state_keys)
        found = bool(tool_context.state.get(state_key))
        logger.info(
            "resource_confirmation: check id_arg=%s actual_resource_id=%s "
            "approved_resource_ids=%s found=%s state_keys=%s",
            id_arg,
            resource_id,
            approved_resource_ids,
            found,
            state_keys,
        )

        if found:
            logger.info(
                "resource_confirmation: decision=approved source=session_state "
                "id_arg=%s actual_resource_id=%s approved_resource_ids=%s",
                id_arg,
                resource_id,
                approved_resource_ids,
            )
            return False

        # ADK re-invokes the tool after the user confirms.  Detect that and
        # store the approval so future calls skip the prompt.
        confirmation = getattr(tool_context, "tool_confirmation", None)
        if confirmation is not None and confirmation.confirmed:
            tool_context.state[state_key] = True
            logger.info(
                "resource_confirmation: decision=approved source=user_confirmation "
                "id_arg=%s actual_resource_id=%s approved_resource_ids_before=%s "
                "stored_state_key=%s",
                id_arg,
                resource_id,
                approved_resource_ids,
                state_key,
            )
            return False

        if confirmation is not None and not confirmation.confirmed:
            # Confirmation was declined (offline auto-decline or interactive user reject).
            # Don't request confirmation again — let the tool run so the model gets
            # the real API error (e.g. 404 for invalid ID) and can recover.
            logger.info(
                "resource_confirmation: decision=declined source=user_confirmation "
                "id_arg=%s actual_resource_id=%s — skipping re-confirmation",
                id_arg,
                resource_id,
            )
            return False

        logger.warning(
            "resource_confirmation: decision=require_confirmation reason=resource_not_preapproved "
            "id_arg=%s actual_resource_id=%s approved_resource_ids=%s missing_state_key=%s",
            id_arg,
            resource_id,
            approved_resource_ids,
            state_key,
        )
        return True

    return _check


sheet_confirmation = make_resource_confirmation("sheet_id")
doc_confirmation = make_resource_confirmation("document_id")
drive_folder_confirmation = make_resource_confirmation("folder_id")
