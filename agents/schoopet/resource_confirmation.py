"""Callable factories for per-resource bulk confirmation.

Passed as ``require_confirmation=make_resource_confirmation(...)`` on FunctionTool.
First call for a given resource ID requests a normal confirmation from the user.
After the user approves, that resource ID is stored in ADK session state and all
subsequent calls for the same resource skip the prompt automatically.

Pre-built singletons for the three standard resource types are exported at the
bottom of this module (sheet_confirmation, doc_confirmation, drive_folder_confirmation).
Prefer importing those over calling make_resource_confirmation directly.

The session-state key format is ``_RESOURCE_CONFIRMED_PREFIX + state_prefix + "_" + resource_id``.
The task-worker mirrors this format to pre-seed approved resource IDs into session state.
"""
from typing import Any

from google.adk.tools import ToolContext

# Session-state key prefix shared with the task-worker service.
# task-worker/src/worker.py mirrors this value to pre-seed approved resources.
_RESOURCE_CONFIRMED_PREFIX = "_resource_confirmed_"


def make_resource_confirmation(id_arg: str, state_prefix: str):
    """Return a require_confirmation callable scoped to a resource ID.

    Args:
        id_arg: Name of the function argument that carries the resource ID
                (e.g. "sheet_id", "document_id", "folder_id").
        state_prefix: Short label used as part of the session-state key
                      (e.g. "sheet", "doc", "drive_folder").
    """
    async def _check(tool_context: ToolContext = None, **kwargs: Any) -> bool:
        resource_id = kwargs.get(id_arg) or "_default_"
        state_key = f"{_RESOURCE_CONFIRMED_PREFIX}{state_prefix}_{resource_id}"

        if tool_context is None:
            return True

        # Already approved this resource earlier in the session.
        if tool_context.state.get(state_key):
            return False

        # ADK re-invokes the tool after the user confirms.  Detect that and
        # store the approval so future calls skip the prompt.
        confirmation = getattr(tool_context, "tool_confirmation", None)
        if confirmation is not None and confirmation.confirmed:
            tool_context.state[state_key] = True
            return False

        return True

    return _check


sheet_confirmation = make_resource_confirmation("sheet_id", "sheet")
doc_confirmation = make_resource_confirmation("document_id", "doc")
drive_folder_confirmation = make_resource_confirmation("folder_id", "drive_folder")
