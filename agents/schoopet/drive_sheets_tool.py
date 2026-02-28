"""Google Drive and Google Sheets tools for the Schoopet agent.

Each user authorizes their own Drive and Sheets access once via OAuth under
the shared "google-workspace" feature (covers both drive.file and spreadsheets
scopes in a single authorization).
"""
import logging
from typing import Optional

from google.adk.tools import ToolContext
from .oauth_client import OAuthClient

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

# Single OAuth feature covering both Drive and Sheets scopes
WORKSPACE_FEATURE = "google-workspace"


class DriveTool:
    """Tool for saving and listing files in the user's Google Drive.

    Uses the user's "google-workspace" feature OAuth token.
    """

    def __init__(self):
        self._oauth_client = OAuthClient()

    def save_file_to_drive(
        self,
        filename: str,
        content: str,
        folder_id: str = "",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Save a text file to the user's Google Drive.

        Args:
            filename: Name for the new file (e.g., "2024-01-15_boss_report.txt").
            content: Text content to save.
            folder_id: Optional Drive folder ID. If empty, saves to root.

        Returns:
            Confirmation with file link, or authorization link if Drive is not connected.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot save to Drive — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._oauth_client.get_valid_access_token(phone, WORKSPACE_FEATURE)
        if not token:
            link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
            return (
                f"Google Drive is not connected. "
                f"Please authorize access:\n{link}\n\nAfter authorizing, try again."
            )

        try:
            import httpx

            metadata: dict = {
                "name": filename,
                "mimeType": "text/plain",
            }
            if folder_id:
                metadata["parents"] = [folder_id]

            with httpx.Client() as client:
                # Multipart upload: metadata + media
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
                resp = client.post(
                    f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": f"multipart/related; boundary={boundary}",
                    },
                    content=body.encode("utf-8"),
                )

                if resp.status_code == 401:
                    link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
                    return f"Drive authorization expired. Re-authorize:\n{link}"

                resp.raise_for_status()
                data = resp.json()

            file_id = data.get("id", "")
            file_link = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
            return f"Saved '{filename}' to Drive.{' Link: ' + file_link if file_link else ''}"

        except Exception as e:
            return f"Error saving to Drive: {e}"

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
            Formatted list of files (name, ID, type, last modified), or auth link.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot list Drive files — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._oauth_client.get_valid_access_token(phone, WORKSPACE_FEATURE)
        if not token:
            link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
            return (
                f"Google Drive is not connected. "
                f"Please authorize access:\n{link}\n\nAfter authorizing, try again."
            )

        try:
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

                if resp.status_code == 401:
                    link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
                    return f"Drive authorization expired. Re-authorize:\n{link}"

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

        except Exception as e:
            return f"Error listing Drive files: {e}"

    def get_drive_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check if Google Drive (and Sheets) is connected for this user.

        Returns:
            Connection status and email, or authorization link if not connected.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot check Drive status — no user_id in tool_context."

        phone = tool_context.user_id
        token_data = self._oauth_client.get_token_data(phone, WORKSPACE_FEATURE)
        if not token_data:
            link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
            return f"Google Workspace (Drive + Sheets) is not connected. Authorize here:\n{link}"

        email = token_data.get("email", "Unknown")
        if self._oauth_client.is_token_expired(token_data):
            refreshed = self._oauth_client.get_valid_access_token(phone, WORKSPACE_FEATURE)
            if not refreshed:
                link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
                return f"Google Workspace connection expired. Re-authorize:\n{link}"

        return f"Google Workspace (Drive + Sheets) is connected ({email})."


class SheetsTool:
    """Tool for reading and writing to the user's Google Sheets.

    Uses the user's "google-workspace" feature OAuth token.
    """

    def __init__(self):
        self._oauth_client = OAuthClient()

    def _get_token(self, phone: str) -> Optional[str]:
        return self._oauth_client.get_valid_access_token(phone, WORKSPACE_FEATURE)

    def _auth_link(self, phone: str) -> str:
        return self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)

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
            Confirmation message or authorization link if not connected.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot append to Sheets — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._get_token(phone)
        if not token:
            return (
                f"Google Sheets is not connected. "
                f"Please authorize access:\n{self._auth_link(phone)}\n\nAfter authorizing, try again."
            )

        try:
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

                if resp.status_code == 401:
                    return f"Sheets authorization expired. Re-authorize:\n{self._auth_link(phone)}"

                resp.raise_for_status()
                data = resp.json()

            updates = data.get("updates", {})
            updated_cells = updates.get("updatedCells", len(values))
            return f"Row appended to sheet ({updated_cells} cells updated)."

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
            Headers row and data rows as a plain-text table, or auth link.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot read sheet — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._get_token(phone)
        if not token:
            return (
                f"Google Sheets is not connected. "
                f"Please authorize access:\n{self._auth_link(phone)}\n\nAfter authorizing, try again."
            )

        try:
            import httpx

            with httpx.Client() as client:
                resp = client.get(
                    f"{SHEETS_API_BASE}/{sheet_id}/values/{sheet_tab}",
                    headers={"Authorization": f"Bearer {token}"},
                )

                if resp.status_code == 401:
                    return f"Sheets authorization expired. Re-authorize:\n{self._auth_link(phone)}"

                resp.raise_for_status()
                data = resp.json()

            rows = data.get("values", [])
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
            Confirmation of which cell was written, or auth link.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot add column — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._get_token(phone)
        if not token:
            return (
                f"Google Sheets is not connected. "
                f"Please authorize access:\n{self._auth_link(phone)}\n\nAfter authorizing, try again."
            )

        try:
            import httpx

            # Read row 1 to find current column count
            with httpx.Client() as client:
                resp = client.get(
                    f"{SHEETS_API_BASE}/{sheet_id}/values/{sheet_tab}!1:1",
                    headers={"Authorization": f"Bearer {token}"},
                )

                if resp.status_code == 401:
                    return f"Sheets authorization expired. Re-authorize:\n{self._auth_link(phone)}"

                resp.raise_for_status()
                row1 = resp.json().get("values", [[]])[0] if resp.json().get("values") else []

            next_col = len(row1) + 1  # 1-indexed
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

                if resp.status_code == 401:
                    return f"Sheets authorization expired. Re-authorize:\n{self._auth_link(phone)}"

                resp.raise_for_status()

            return f"Column '{column_header}' added at {cell_range}."

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
            Confirmation of the cell updated, or auth link.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot update cell — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._get_token(phone)
        if not token:
            return (
                f"Google Sheets is not connected. "
                f"Please authorize access:\n{self._auth_link(phone)}\n\nAfter authorizing, try again."
            )

        try:
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

                if resp.status_code == 401:
                    return f"Sheets authorization expired. Re-authorize:\n{self._auth_link(phone)}"

                resp.raise_for_status()

            return f"Cell {cell_range} updated to '{value}'."

        except Exception as e:
            return f"Error updating cell: {e}"

    def get_sheets_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check if Google Sheets (and Drive) is connected for this user.

        Returns:
            Connection status and email, or authorization link if not connected.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot check Sheets status — no user_id in tool_context."

        phone = tool_context.user_id
        token_data = self._oauth_client.get_token_data(phone, WORKSPACE_FEATURE)
        if not token_data:
            link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
            return f"Google Workspace (Drive + Sheets) is not connected. Authorize here:\n{link}"

        email = token_data.get("email", "Unknown")
        if self._oauth_client.is_token_expired(token_data):
            refreshed = self._oauth_client.get_valid_access_token(phone, WORKSPACE_FEATURE)
            if not refreshed:
                link = self._oauth_client.get_oauth_link(phone, WORKSPACE_FEATURE)
                return f"Google Workspace connection expired. Re-authorize:\n{link}"

        return f"Google Workspace (Drive + Sheets) is connected ({email})."


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
