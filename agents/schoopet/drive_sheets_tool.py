"""Google Drive and Google Sheets tools for the Schoopet agent.

Access mode determines which token is used at construction time:
- "personal": user's personal OAuth token (feature="google-workspace")
- "team": system token (feature="workspace_system", user="email_system")

No runtime fallback. Each instance uses exactly one token path.
"""
import logging
from typing import Optional, Literal

from google.adk.tools import ToolContext
from .oauth_client import OAuthClient
from .utils import require_user_id

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

# Single OAuth feature covering both Drive and Sheets scopes (personal user)
WORKSPACE_FEATURE = "google-workspace"

# System-level shared agent account constants
WORKSPACE_SYSTEM_USER_ID = "email_system"
WORKSPACE_SYSTEM_FEATURE = "workspace_system"


class DriveTool:
    """Tool for saving and listing files in Google Drive.

    Access mode is set at construction time:
    - "personal": uses the user's personal google-workspace token
    - "team": uses the shared workspace_system token (system account)
    """

    def __init__(self, access_mode: Literal["personal", "team"] = "personal"):
        self._oauth_client = OAuthClient()
        self._access_mode = access_mode

    def _get_token(self, user_id: str):
        """Return (token, error) using exactly one token path."""
        if self._access_mode == "team":
            token = self._oauth_client.get_valid_access_token(
                WORKSPACE_SYSTEM_USER_ID, WORKSPACE_SYSTEM_FEATURE
            )
            return token, (None if token else "Team workspace not connected. Contact admin.")
        token = self._oauth_client.get_valid_access_token(user_id, WORKSPACE_FEATURE)
        return token, (None if token else self._oauth_client.get_oauth_link(user_id, WORKSPACE_FEATURE))

    # ── Private token-specific helpers ─────────────────────────────────────────

    def _save_file_with_token(
        self,
        token: str,
        filename: str,
        content: str,
        folder_id: str,
    ) -> Optional[str]:
        """Attempt to save a file using the given token. Returns None on 401/403."""
        import httpx

        metadata: dict = {
            "name": filename,
            "mimeType": "text/plain",
        }
        if folder_id:
            metadata["parents"] = [folder_id]

        boundary = "schoopet_boundary"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + _json_dumps(metadata)
            + f"\r\n--{boundary}\r\n"
            f"Content-Type: text/plain; charset=UTF-8\r\n\r\n"
            + content
            + f"\r\n--{boundary}--"
        )

        with httpx.Client() as client:
            resp = client.post(
                f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                content=body.encode("utf-8"),
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        data = resp.json()
        file_id = data.get("id", "")
        file_link = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
        return f"Saved '{filename}' to Drive.{' Link: ' + file_link if file_link else ''}"

    def _save_binary_with_token(
        self,
        token: str,
        filename: str,
        raw_bytes: bytes,
        mime_type: str,
        folder_id: str,
    ) -> Optional[str]:
        """Attempt to save a binary file using the given token. Returns None on 401/403."""
        import httpx
        import json

        metadata: dict = {
            "name": filename,
            "mimeType": mime_type,
        }
        if folder_id:
            metadata["parents"] = [folder_id]

        boundary = b"schoopet_boundary"
        body = (
            b"--" + boundary + b"\r\n"
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps(metadata).encode("utf-8")
            + b"\r\n--" + boundary + b"\r\n"
            b"Content-Type: " + mime_type.encode("utf-8") + b"\r\n\r\n"
            + raw_bytes
            + b"\r\n--" + boundary + b"--"
        )

        boundary_str = boundary.decode("utf-8")
        with httpx.Client() as client:
            resp = client.post(
                f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary_str}",
                },
                content=body,
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        data = resp.json()
        file_id = data.get("id", "")
        file_link = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
        return f"Saved '{filename}' to Drive.{' Link: ' + file_link if file_link else ''}"

    def _list_files_with_token(
        self,
        token: str,
        folder_id: str,
        query: str,
        max_results: int,
    ) -> Optional[str]:
        """Attempt to list files using the given token. Returns None on 401/403."""
        import httpx

        q = f"'{folder_id}' in parents and trashed=false"
        if query:
            q += f" and {query}"

        with httpx.Client() as client:
            resp = client.get(
                f"{DRIVE_API_BASE}/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": q,
                    "pageSize": max_results,
                    "fields": "files(id,name,mimeType,modifiedTime)",
                    "orderBy": "modifiedTime desc",
                },
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        files = resp.json().get("files", [])

        if not files:
            return "No files found in that folder."

        lines = [f"Files in folder ({len(files)}):"]
        for f in files:
            modified = f.get("modifiedTime", "")[:10]
            mime = f.get("mimeType", "")
            type_label = _mime_label(mime)
            lines.append(f"  {f['name']} | ID: {f['id']} | {type_label} | Modified: {modified}")
        return "\n".join(lines)

    # ── Public methods ──────────────────────────────────────────────────────────

    def save_file_to_drive(
        self,
        filename: str,
        content: str,
        folder_id: str = "",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Save a text file to Google Drive.

        Args:
            filename: Name for the new file (e.g., "2024-01-15_boss_report.txt").
            content: Text content to save.
            folder_id: Optional Drive folder ID. If empty, saves to root.

        Returns:
            Confirmation with file link, or instructions if Drive is not accessible.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "drive")
        if err:
            return err

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._save_file_with_token(token, filename, content, folder_id)
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error saving to Drive: {e}"

    async def save_attachment_to_drive(
        self,
        artifact_filename: str,
        drive_filename: str,
        folder_id: str = "",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Save a binary email attachment (PDF, image, etc.) from the artifact
        registry to Google Drive, preserving the original file format.

        Use this for attachments listed in the email prompt under 'Stored attachments'.
        This uploads the original binary rather than the text-extracted version.

        Args:
            artifact_filename: The artifact key exactly as listed in the email prompt
                (e.g., "abc123_resume.pdf").
            drive_filename: Desired filename in Drive (e.g., "resume_john.pdf").
            folder_id: Optional Drive folder ID. If empty, saves to root.

        Returns:
            Confirmation with file link, or error/instructions if not accessible.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "drive")
        if err:
            return err

        artifact = await tool_context.load_artifact(artifact_filename)
        if not artifact or not artifact.inline_data:
            return f"ERROR: Artifact '{artifact_filename}' not found or has no binary data."

        raw_bytes = artifact.inline_data.data
        mime_type = artifact.inline_data.mime_type or "application/octet-stream"

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._save_binary_with_token(
                token, drive_filename, raw_bytes, mime_type, folder_id
            )
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error saving attachment to Drive: {e}"

    def list_drive_files(
        self,
        folder_id: str,
        query: str = "",
        max_results: int = 20,
        tool_context: ToolContext = None,
    ) -> str:
        """
        List files in a Google Drive folder.

        Use this to check whether a file already exists before saving a duplicate.

        Args:
            folder_id: Drive folder ID to list files from.
            query: Optional extra filter clause (e.g., "name contains 'invoice'").
            max_results: Maximum number of files to return (default: 20).

        Returns:
            Formatted list of files (name, ID, type, last modified), or access instructions.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "drive")
        if err:
            return err

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._list_files_with_token(token, folder_id, query, max_results)
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error listing Drive files: {e}"

    def get_drive_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check if Google Drive (and Sheets) is connected.

        Returns:
            Connection status based on access mode.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "drive")
        if err:
            return err

        if self._access_mode == "team":
            token = self._oauth_client.get_valid_access_token(
                WORKSPACE_SYSTEM_USER_ID, WORKSPACE_SYSTEM_FEATURE
            )
            if token:
                return "Team workspace (system account) is connected."
            return "Team workspace not connected. Contact admin."

        # Personal mode
        user_token_data = self._oauth_client.get_token_data(phone, WORKSPACE_FEATURE)
        if user_token_data:
            email = user_token_data.get("email", "Unknown")
            return f"Personal workspace ({email}) is connected."
        link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
        return f"Personal workspace not connected. Authorize here:\n{link}"


class SheetsTool:
    """Tool for reading and writing to Google Sheets.

    Access mode is set at construction time:
    - "personal": uses the user's personal google-workspace token
    - "team": uses the shared workspace_system token (system account)
    """

    def __init__(self, access_mode: Literal["personal", "team"] = "personal"):
        self._oauth_client = OAuthClient()
        self._access_mode = access_mode

    def _get_token(self, user_id: str):
        """Return (token, error) using exactly one token path."""
        if self._access_mode == "team":
            token = self._oauth_client.get_valid_access_token(
                WORKSPACE_SYSTEM_USER_ID, WORKSPACE_SYSTEM_FEATURE
            )
            return token, (None if token else "Team workspace not connected. Contact admin.")
        token = self._oauth_client.get_valid_access_token(user_id, WORKSPACE_FEATURE)
        return token, (None if token else self._oauth_client.get_oauth_link(user_id, WORKSPACE_FEATURE))

    # ── Private token-specific helpers ─────────────────────────────────────────

    def _read_sheet_with_token(
        self,
        token: str,
        sheet_id: str,
        sheet_tab: str,
        max_rows: int,
    ) -> Optional[str]:
        """Attempt to read a sheet using the given token. Returns None on 401/403."""
        import httpx

        with httpx.Client() as client:
            resp = client.get(
                f"{SHEETS_API_BASE}/{sheet_id}/values/{sheet_tab}",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        rows = resp.json().get("values", [])
        if not rows:
            return "Sheet is empty."

        truncated = rows[:max_rows]
        lines = []
        for i, row in enumerate(truncated):
            prefix = "HDR" if i == 0 else f"R{i:>3}"
            lines.append(f"{prefix}: {' | '.join(str(c) for c in row)}")

        result = "\n".join(lines)
        if len(rows) > max_rows:
            result += f"\n... ({len(rows) - max_rows} more rows not shown)"
        return result

    def _append_row_with_token(
        self,
        token: str,
        values: list,
        sheet_id: str,
        sheet_tab: str,
    ) -> Optional[str]:
        """Attempt to append a row using the given token. Returns None on 401/403."""
        import httpx

        range_name = f"{sheet_tab}!A:Z"
        with httpx.Client() as client:
            resp = client.post(
                f"{SHEETS_API_BASE}/{sheet_id}/values/{range_name}:append",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": [values]},
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        data = resp.json()
        updated_cells = data.get("updates", {}).get("updatedCells", len(values))
        return f"Row appended to sheet ({updated_cells} cells updated)."

    def _add_column_with_token(
        self,
        token: str,
        sheet_id: str,
        column_header: str,
        sheet_tab: str,
    ) -> Optional[str]:
        """Attempt to add a column using the given token. Returns None on 401/403."""
        import httpx

        with httpx.Client() as client:
            resp = client.get(
                f"{SHEETS_API_BASE}/{sheet_id}/values/{sheet_tab}!1:1",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        row1 = resp.json().get("values", [[]])[0] if resp.json().get("values") else []

        next_col = len(row1) + 1
        col_letter = _col_to_letter(next_col)
        cell_range = f"{sheet_tab}!{col_letter}1"

        with httpx.Client() as client:
            resp = client.put(
                f"{SHEETS_API_BASE}/{sheet_id}/values/{cell_range}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                params={"valueInputOption": "RAW"},
                json={"values": [[column_header]]},
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        return f"Column '{column_header}' added at {cell_range}."

    def _update_cell_with_token(
        self,
        token: str,
        sheet_id: str,
        row: int,
        column: int,
        value: str,
        sheet_tab: str,
    ) -> Optional[str]:
        """Attempt to update a cell using the given token. Returns None on 401/403."""
        import httpx

        col_letter = _col_to_letter(column)
        cell_range = f"{sheet_tab}!{col_letter}{row}"

        with httpx.Client() as client:
            resp = client.put(
                f"{SHEETS_API_BASE}/{sheet_id}/values/{cell_range}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                params={"valueInputOption": "RAW"},
                json={"values": [[value]]},
            )

        if resp.status_code in (401, 403):
            return None

        resp.raise_for_status()
        return f"Cell {cell_range} updated to '{value}'."

    # ── Public methods ──────────────────────────────────────────────────────────

    def append_row_to_sheet(
        self,
        values: list,
        sheet_id: str,
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Append a row of values to a Google Sheet.

        Args:
            values: List of cell values to append as a new row.
            sheet_id: The Google Sheets document ID (from the URL).
            sheet_tab: Name of the sheet tab (default: "Sheet1").

        Returns:
            Confirmation message or access instructions if not connected.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._append_row_with_token(token, values, sheet_id, sheet_tab)
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error appending to Sheets: {e}"

    def read_sheet(
        self,
        sheet_id: str,
        sheet_tab: str = "Sheet1",
        max_rows: int = 100,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Read current data and headers from a Google Sheet.

        Call this before appending a row to verify the column layout, or to
        inspect existing data.

        Args:
            sheet_id: The Google Sheets document ID (from the URL).
            sheet_tab: Name of the sheet tab (default: "Sheet1").
            max_rows: Maximum number of rows to return (default: 100).

        Returns:
            Headers row and data rows as a plain-text table, or access instructions.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._read_sheet_with_token(token, sheet_id, sheet_tab, max_rows)
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error reading sheet: {e}"

    def add_sheet_column(
        self,
        sheet_id: str,
        column_header: str,
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Append a new column header to row 1 of a Google Sheet.

        Internally reads the current row 1 to find the next empty column, then
        writes the header there.

        Args:
            sheet_id: The Google Sheets document ID (from the URL).
            column_header: The header text for the new column.
            sheet_tab: Name of the sheet tab (default: "Sheet1").

        Returns:
            Confirmation of which cell was written, or access instructions.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._add_column_with_token(token, sheet_id, column_header, sheet_tab)
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error adding column: {e}"

    def update_sheet_cell(
        self,
        sheet_id: str,
        row: int,
        column: int,
        value: str,
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Update a specific cell in a Google Sheet by row and column number.

        Args:
            sheet_id: The Google Sheets document ID (from the URL).
            row: 1-indexed row number (row 1 is the header row).
            column: 1-indexed column number (column 1 = A).
            value: New value to write into the cell.
            sheet_tab: Name of the sheet tab (default: "Sheet1").

        Returns:
            Confirmation of the cell updated, or access instructions.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        token, error = self._get_token(phone)
        if token is None:
            return error

        try:
            result = self._update_cell_with_token(
                token, sheet_id, row, column, value, sheet_tab
            )
            if result is None:
                return error
            return result
        except Exception as e:
            return f"Error updating cell: {e}"

    def get_sheets_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check if Google Sheets (and Drive) is connected.

        Returns:
            Connection status based on access mode.

        Note:
            Requires user_id from tool_context (phone number).
        """
        phone, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        if self._access_mode == "team":
            token = self._oauth_client.get_valid_access_token(
                WORKSPACE_SYSTEM_USER_ID, WORKSPACE_SYSTEM_FEATURE
            )
            if token:
                return "Team workspace (system account) is connected."
            return "Team workspace not connected. Contact admin."

        # Personal mode
        user_token_data = self._oauth_client.get_token_data(phone, WORKSPACE_FEATURE)
        if user_token_data:
            email = user_token_data.get("email", "Unknown")
            return f"Personal workspace ({email}) is connected."
        link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
        return f"Personal workspace not connected. Authorize here:\n{link}"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _col_to_letter(col: int) -> str:
    """Convert 1-indexed column number to A1-notation letter(s). E.g. 1→A, 26→Z, 27→AA."""
    letters = ""
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _mime_label(mime: str) -> str:
    labels = {
        "application/vnd.google-apps.spreadsheet": "Google Sheet",
        "application/vnd.google-apps.document": "Google Doc",
        "application/vnd.google-apps.folder": "Folder",
        "text/plain": "Text",
        "application/pdf": "PDF",
    }
    return labels.get(mime, mime.split("/")[-1] if "/" in mime else mime)


def _json_dumps(obj: dict) -> str:
    import json
    return json.dumps(obj)
