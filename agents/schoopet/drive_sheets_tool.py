"""Google Drive and Google Sheets tools for the Schoopet agent.

Uses IAM connector credentials for the calling user's personal Google account.
"""
import logging
from typing import Optional, List, Dict, Any

from google.adk.auth.credential_manager import CredentialManager
from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .gcp_auth import get_credential_manager
from .utils import require_user_id

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"


class DriveTool:
    """Tool for saving and listing files in Google Drive using IAM connector credentials."""

    def __init__(self):
        self._cred_manager: CredentialManager | None = None

    def _get_credential_manager(self) -> CredentialManager:
        if self._cred_manager is None:
            self._cred_manager = get_credential_manager()
        return self._cred_manager

    async def _get_service(self, tool_context: ToolContext):
        """Return Drive service, or None after emitting a credential request."""
        cred_mgr = self._get_credential_manager()
        try:
            credential = await cred_mgr.get_auth_credential(tool_context)
        except Exception:
            await cred_mgr.request_credential(tool_context)
            return None
        if not credential:
            await cred_mgr.request_credential(tool_context)
            return None
        from google.oauth2.credentials import Credentials
        creds = Credentials(token=credential.http.credentials.token)
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    # ── Private token-specific helpers ─────────────────────────────────────────

    def _save_file_with_token(
        self,
        service,
        filename: str,
        content: str,
        folder_id: str,
    ) -> str:
        """Save a text file using the Google Drive service client."""
        metadata: dict = {
            "name": filename,
            "mimeType": "text/plain",
        }
        if folder_id:
            metadata["parents"] = [folder_id]

        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(
            content.encode("utf-8"),
            mimetype="text/plain",
            resumable=False,
        )
        data = (
            service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id",
            )
            .execute()
        )
        file_id = data.get("id", "")
        file_link = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
        return f"Saved '{filename}' to Drive.{' Link: ' + file_link if file_link else ''}"

    def _save_binary_with_token(
        self,
        service,
        filename: str,
        raw_bytes: bytes,
        mime_type: str,
        folder_id: str,
    ) -> str:
        """Save a binary file using the Google Drive service client."""
        metadata: dict = {
            "name": filename,
            "mimeType": mime_type,
        }
        if folder_id:
            metadata["parents"] = [folder_id]

        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(
            raw_bytes,
            mimetype=mime_type,
            resumable=False,
        )
        data = (
            service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id",
            )
            .execute()
        )
        file_id = data.get("id", "")
        file_link = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
        return f"Saved '{filename}' to Drive.{' Link: ' + file_link if file_link else ''}"

    def _list_files_with_token(
        self,
        service,
        folder_id: str,
        query: str,
        max_results: int,
    ) -> str:
        """List files using the Google Drive service client."""
        q = f"'{folder_id}' in parents and trashed=false"
        if query:
            q += f" and {query}"

        result = (
            service.files()
            .list(
                q=q,
                pageSize=max_results,
                fields="files(id,name,mimeType,modifiedTime)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        files = result.get("files", [])

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

    async def save_file_to_drive(
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "drive")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return self._save_file_with_token(service, filename, content, folder_id)
        except HttpError as e:
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "drive")
        if err:
            return err

        artifact = await tool_context.load_artifact(artifact_filename)
        if not artifact or not artifact.inline_data:
            return f"ERROR: Artifact '{artifact_filename}' not found or has no binary data."

        raw_bytes = artifact.inline_data.data
        mime_type = artifact.inline_data.mime_type or "application/octet-stream"

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            result = self._save_binary_with_token(
                service, drive_filename, raw_bytes, mime_type, folder_id
            )
            return result
        except HttpError as e:
            return f"Error saving attachment to Drive: {e}"

    async def list_drive_files(
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "drive")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return self._list_files_with_token(service, folder_id, query, max_results)
        except HttpError as e:
            return f"Error listing Drive files: {e}"

    async def get_drive_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check if Google Drive (and Sheets) is connected.

        Returns:
            Connection status based on access mode.

        Note:
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "drive")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""
        return "Google Workspace (Drive, Sheets, Docs) is connected."


class SheetsTool:
    """Tool for reading and writing to Google Sheets using IAM connector credentials."""

    def __init__(self):
        self._cred_manager: CredentialManager | None = None

    def _get_credential_manager(self) -> CredentialManager:
        if self._cred_manager is None:
            self._cred_manager = get_credential_manager()
        return self._cred_manager

    async def _get_service(self, tool_context: ToolContext):
        """Return Sheets service, or None after emitting a credential request."""
        cred_mgr = self._get_credential_manager()
        try:
            credential = await cred_mgr.get_auth_credential(tool_context)
        except Exception:
            await cred_mgr.request_credential(tool_context)
            return None
        if not credential:
            await cred_mgr.request_credential(tool_context)
            return None
        from google.oauth2.credentials import Credentials
        creds = Credentials(token=credential.http.credentials.token)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    # ── Private token-specific helpers ─────────────────────────────────────────

    def _get_sheet_values(
        self,
        service,
        sheet_id: str,
        range_name: str,
    ) -> List[List[str]]:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=range_name)
            .execute()
        )
        return result.get("values", [])

    def _get_header_row(
        self,
        service,
        sheet_id: str,
        sheet_tab: str,
    ) -> List[str]:
        rows = self._get_sheet_values(service, sheet_id, f"{sheet_tab}!1:1")
        return rows[0] if rows else []

    def _create_spreadsheet_with_token(
        self,
        service,
        title: str,
        sheet_tab: str,
        headers: List[str],
    ) -> Dict[str, Any]:
        body = {
            "properties": {"title": title},
            "sheets": [{"properties": {"title": sheet_tab}}],
        }
        result = (
            service.spreadsheets()
            .create(body=body)
            .execute()
        )

        sheet_id = result.get("spreadsheetId", "")
        spreadsheet_url = result.get("spreadsheetUrl", "")
        if headers and sheet_id:
            self._ensure_headers_with_token(service, sheet_id, headers, sheet_tab)

        return {
            "spreadsheet_id": sheet_id,
            "spreadsheet_url": spreadsheet_url,
            "title": title,
            "sheet_tab": sheet_tab,
            "headers": headers,
        }

    def _add_sheet_tab_with_token(
        self,
        service,
        sheet_id: str,
        sheet_tab: str,
        headers: List[str],
    ) -> Dict[str, Any]:
        (
            service.spreadsheets()
            .batchUpdate(
                spreadsheetId=sheet_id,
                body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": sheet_tab,
                                }
                            }
                        }
                    ]
                },
            )
            .execute()
        )
        if headers:
            self._ensure_headers_with_token(service, sheet_id, headers, sheet_tab)

        return {
            "spreadsheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "headers": headers,
        }

    def _read_sheet_with_token(
        self,
        service,
        sheet_id: str,
        sheet_tab: str,
        max_rows: int,
    ) -> str:
        """Read a sheet using the Google Sheets service client."""
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=sheet_tab)
            .execute()
        )
        rows = result.get("values", [])
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
        service,
        values: List[str],
        sheet_id: str,
        sheet_tab: str,
    ) -> str:
        """Append a row using the Google Sheets service client."""
        range_name = f"{sheet_tab}!A:Z"
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=sheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": [values]},
            )
            .execute()
        )
        data = result
        updated_cells = data.get("updates", {}).get("updatedCells", len(values))
        return f"Row appended to sheet ({updated_cells} cells updated)."

    def _add_column_with_token(
        self,
        service,
        sheet_id: str,
        column_header: str,
        sheet_tab: str,
    ) -> str:
        """Add a header cell using the Google Sheets service client."""
        row1 = self._get_header_row(service, sheet_id, sheet_tab)

        next_col = len(row1) + 1
        col_letter = _col_to_letter(next_col)
        cell_range = f"{sheet_tab}!{col_letter}1"

        (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=sheet_id,
                range=cell_range,
                valueInputOption="RAW",
                body={"values": [[column_header]]},
            )
            .execute()
        )
        return f"Column '{column_header}' added at {cell_range}."

    def _update_cell_with_token(
        self,
        service,
        sheet_id: str,
        row: int,
        column: int,
        value: str,
        sheet_tab: str,
    ) -> str:
        """Update a cell using the Google Sheets service client."""
        col_letter = _col_to_letter(column)
        cell_range = f"{sheet_tab}!{col_letter}{row}"

        (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=sheet_id,
                range=cell_range,
                valueInputOption="RAW",
                body={"values": [[value]]},
            )
            .execute()
        )
        return f"Cell {cell_range} updated to '{value}'."

    def _sheet_metadata_with_token(
        self,
        service,
        sheet_id: str,
        sheet_tab: str,
    ) -> Dict[str, Any]:
        rows = self._get_sheet_values(service, sheet_id, sheet_tab)
        headers = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []
        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "headers": headers,
            "header_count": len(headers),
            "row_count": len(rows),
            "data_row_count": len(data_rows),
        }

    def _read_records_with_token(
        self,
        service,
        sheet_id: str,
        sheet_tab: str,
        max_rows: int,
    ) -> Dict[str, Any]:
        rows = self._get_sheet_values(service, sheet_id, sheet_tab)
        headers = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []
        truncated_rows = data_rows[:max_rows]
        records = [_row_to_record(headers, row) for row in truncated_rows]
        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "headers": headers,
            "records": records,
            "returned_records": len(records),
            "remaining_records": max(0, len(data_rows) - len(records)),
        }

    def _ensure_headers_with_token(
        self,
        service,
        sheet_id: str,
        headers: List[str],
        sheet_tab: str,
    ) -> Dict[str, Any]:
        current_headers = self._get_header_row(service, sheet_id, sheet_tab)
        missing_headers = [header for header in headers if header not in current_headers]
        added_headers: List[str] = []

        for header in missing_headers:
            self._add_column_with_token(service, sheet_id, header, sheet_tab)
            added_headers.append(header)
            current_headers.append(header)

        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "headers": current_headers,
            "added_headers": added_headers,
        }

    def _append_record_with_token(
        self,
        service,
        sheet_id: str,
        record: Dict[str, Any],
        sheet_tab: str,
    ) -> Dict[str, Any]:
        current_headers = self._get_header_row(service, sheet_id, sheet_tab)
        if not current_headers:
            current_headers = list(record.keys())
            self._ensure_headers_with_token(service, sheet_id, current_headers, sheet_tab)
        else:
            self._ensure_headers_with_token(service, sheet_id, list(record.keys()), sheet_tab)
            current_headers = self._get_header_row(service, sheet_id, sheet_tab)

        values = [_stringify_cell(record.get(header, "")) for header in current_headers]
        append_result = self._append_row_with_token(service, values, sheet_id, sheet_tab)
        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "headers": current_headers,
            "record": {header: _stringify_cell(record.get(header, "")) for header in current_headers},
            "result": append_result,
        }

    def _find_rows_with_token(
        self,
        service,
        sheet_id: str,
        match_column: str,
        match_value: str,
        sheet_tab: str,
        max_rows: int,
    ) -> Dict[str, Any]:
        rows = self._get_sheet_values(service, sheet_id, sheet_tab)
        headers = rows[0] if rows else []
        if not headers:
            return {
                "sheet_id": sheet_id,
                "sheet_tab": sheet_tab,
                "match_column": match_column,
                "match_value": match_value,
                "matches": [],
                "returned_matches": 0,
            }

        if match_column not in headers:
            raise ValueError(f"Column '{match_column}' not found. Headers: {', '.join(headers)}")

        col_index = headers.index(match_column)
        matches = []
        for row_number, row in enumerate(rows[1:], start=2):
            cell_value = row[col_index] if col_index < len(row) else ""
            if str(cell_value) == str(match_value):
                matches.append({
                    "row_number": row_number,
                    "record": _row_to_record(headers, row),
                })

        truncated = matches[:max_rows]
        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "match_column": match_column,
            "match_value": match_value,
            "matches": truncated,
            "returned_matches": len(truncated),
            "remaining_matches": max(0, len(matches) - len(truncated)),
        }

    def _update_row_with_token(
        self,
        service,
        sheet_id: str,
        row: int,
        updates: Dict[str, Any],
        sheet_tab: str,
    ) -> Dict[str, Any]:
        headers = self._get_header_row(service, sheet_id, sheet_tab)
        if not headers:
            raise ValueError("Sheet has no header row.")

        missing_headers = [header for header in updates if header not in headers]
        if missing_headers:
            raise ValueError(f"Unknown columns: {', '.join(missing_headers)}")

        updated_cells = []
        for header, value in updates.items():
            col_number = headers.index(header) + 1
            self._update_cell_with_token(
                service,
                sheet_id,
                row,
                col_number,
                _stringify_cell(value),
                sheet_tab,
            )
            updated_cells.append(f"{header}={_stringify_cell(value)}")

        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "row": row,
            "updated_cells": updated_cells,
        }

    # ── Public methods ──────────────────────────────────────────────────────────

    async def append_row_to_sheet(
        self,
        values: List[str],
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return self._append_row_with_token(service, values, sheet_id, sheet_tab)
        except HttpError as e:
            return f"Error appending to Sheets: {e}"

    async def read_sheet(
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return self._read_sheet_with_token(service, sheet_id, sheet_tab, max_rows)
        except HttpError as e:
            return f"Error reading sheet: {e}"

    async def add_sheet_column(
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return self._add_column_with_token(service, sheet_id, column_header, sheet_tab)
        except HttpError as e:
            return f"Error adding column: {e}"

    async def update_sheet_cell(
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
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return self._update_cell_with_token(
                service, sheet_id, row, column, value, sheet_tab
            )
        except HttpError as e:
            return f"Error updating cell: {e}"

    async def get_sheets_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check if Google Sheets (and Drive) is connected.

        Returns:
            Connection status based on access mode.

        Note:
            Requires user_id from tool_context.
        """
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""
        return "Google Workspace (Drive, Sheets, Docs) is connected."

    async def create_spreadsheet(
        self,
        title: str,
        sheet_tab: str = "Sheet1",
        headers: Optional[List[str]] = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Create a new spreadsheet and optionally initialize its headers."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._create_spreadsheet_with_token(
                    service,
                    title,
                    sheet_tab,
                    headers or [],
                )
            )
        except HttpError as e:
            return f"Error creating spreadsheet: {e}"

    async def add_sheet_tab(
        self,
        sheet_id: str,
        sheet_tab: str,
        headers: Optional[List[str]] = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Add a new tab to an existing spreadsheet and optionally initialize headers."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._add_sheet_tab_with_token(
                    service,
                    sheet_id,
                    sheet_tab,
                    headers or [],
                )
            )
        except HttpError as e:
            return f"Error adding sheet tab: {e}"

    async def get_sheet_schema(
        self,
        sheet_id: str,
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """Return sheet headers and row counts as JSON."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(self._sheet_metadata_with_token(service, sheet_id, sheet_tab))
        except HttpError as e:
            return f"Error reading sheet schema: {e}"

    async def read_sheet_records(
        self,
        sheet_id: str,
        sheet_tab: str = "Sheet1",
        max_rows: int = 100,
        tool_context: ToolContext = None,
    ) -> str:
        """Return sheet rows as JSON records keyed by header."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._read_records_with_token(service, sheet_id, sheet_tab, max_rows)
            )
        except HttpError as e:
            return f"Error reading sheet records: {e}"

    async def ensure_sheet_headers(
        self,
        sheet_id: str,
        headers: List[str],
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """Ensure the given headers exist in row 1 and return the final header list."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._ensure_headers_with_token(service, sheet_id, headers, sheet_tab)
            )
        except HttpError as e:
            return f"Error ensuring sheet headers: {e}"

    async def append_record_to_sheet(
        self,
        record: Dict[str, Any],
        sheet_id: str,
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """Append a record dict keyed by header name."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._append_record_with_token(service, sheet_id, record, sheet_tab)
            )
        except HttpError as e:
            return f"Error appending record to sheet: {e}"

    async def find_sheet_rows(
        self,
        sheet_id: str,
        match_column: str,
        match_value: str,
        sheet_tab: str = "Sheet1",
        max_rows: int = 25,
        tool_context: ToolContext = None,
    ) -> str:
        """Find rows where one header-mapped column equals the given value."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._find_rows_with_token(
                    service, sheet_id, match_column, match_value, sheet_tab, max_rows
                )
            )
        except (HttpError, ValueError) as e:
            return f"Error finding sheet rows: {e}"

    async def update_sheet_row(
        self,
        sheet_id: str,
        row: int,
        updates: Dict[str, Any],
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """Update one row using a dict of header name to value."""
        _, err = require_user_id(tool_context, "sheets")
        if err:
            return err

        service = await self._get_service(tool_context)
        if service is None:
            return ""

        try:
            return _json_dumps(
                self._update_row_with_token(service, sheet_id, row, updates, sheet_tab)
            )
        except (HttpError, ValueError) as e:
            return f"Error updating sheet row: {e}"


class DocsTool:
    """Tool for creating and editing Google Docs using IAM connector credentials."""

    def __init__(self):
        self._cred_manager: CredentialManager | None = None

    def _get_credential_manager(self) -> CredentialManager:
        if self._cred_manager is None:
            self._cred_manager = get_credential_manager()
        return self._cred_manager

    async def _get_services(self, tool_context: ToolContext):
        """Return (docs_service, drive_service) or (None, None) after emitting credential request."""
        cred_mgr = self._get_credential_manager()
        try:
            credential = await cred_mgr.get_auth_credential(tool_context)
        except Exception:
            await cred_mgr.request_credential(tool_context)
            return None, None
        if not credential:
            await cred_mgr.request_credential(tool_context)
            return None, None
        from google.oauth2.credentials import Credentials
        creds = Credentials(token=credential.http.credentials.token)
        docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
        drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return docs_service, drive_service

    def _create_google_doc_with_token(
        self,
        docs_service,
        drive_service,
        title: str,
        content: str,
        folder_id: str,
    ) -> Dict[str, Any]:
        file_metadata: Dict[str, Any] = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        }
        if folder_id:
            file_metadata["parents"] = [folder_id]

        file_result = (
            drive_service.files()
            .create(body=file_metadata, fields="id,webViewLink")
            .execute()
        )
        document_id = file_result.get("id", "")
        document_url = file_result.get("webViewLink", "")

        if content and document_id:
            self._append_to_google_doc_with_token(docs_service, document_id, content)

        return {
            "document_id": document_id,
            "document_url": document_url,
            "title": title,
            "folder_id": folder_id,
        }

    def _extract_document_text(self, document: Dict[str, Any]) -> str:
        pieces: List[str] = []
        for element in document.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            for item in paragraph.get("elements", []):
                text_run = item.get("textRun")
                if text_run:
                    pieces.append(text_run.get("content", ""))
        return "".join(pieces)

    def _read_google_doc_with_token(
        self,
        docs_service,
        document_id: str,
    ) -> Dict[str, Any]:
        document = docs_service.documents().get(documentId=document_id).execute()
        return {
            "document_id": document_id,
            "title": document.get("title", ""),
            "text": self._extract_document_text(document),
        }

    def _append_to_google_doc_with_token(
        self,
        docs_service,
        document_id: str,
        content: str,
    ) -> Dict[str, Any]:
        document = docs_service.documents().get(documentId=document_id).execute()
        end_index = max(1, document.get("body", {}).get("content", [{}])[-1].get("endIndex", 1) - 1)
        (
            docs_service.documents()
            .batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": end_index},
                                "text": content,
                            }
                        }
                    ]
                },
            )
            .execute()
        )
        return {
            "document_id": document_id,
            "appended_characters": len(content),
        }

    def _replace_text_in_google_doc_with_token(
        self,
        docs_service,
        document_id: str,
        search_text: str,
        replace_text: str,
    ) -> Dict[str, Any]:
        result = (
            docs_service.documents()
            .batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "replaceAllText": {
                                "containsText": {
                                    "text": search_text,
                                    "matchCase": True,
                                },
                                "replaceText": replace_text,
                            }
                        }
                    ]
                },
            )
            .execute()
        )
        occurrences_changed = (
            result.get("replies", [{}])[0]
            .get("replaceAllText", {})
            .get("occurrencesChanged", 0)
        )
        return {
            "document_id": document_id,
            "search_text": search_text,
            "replace_text": replace_text,
            "occurrences_changed": occurrences_changed,
        }

    async def create_google_doc(
        self,
        title: str,
        content: str = "",
        folder_id: str = "",
        tool_context: ToolContext = None,
    ) -> str:
        """Create a Google Doc and optionally seed it with text."""
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, drive_service = await self._get_services(tool_context)
        if docs_service is None or drive_service is None:
            return ""

        try:
            return _json_dumps(
                self._create_google_doc_with_token(
                    docs_service,
                    drive_service,
                    title,
                    content,
                    folder_id,
                )
            )
        except HttpError as e:
            return f"Error creating Google Doc: {e}"

    async def read_google_doc(
        self,
        document_id: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Read a Google Doc and return its text."""
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, _ = await self._get_services(tool_context)
        if docs_service is None:
            return ""

        try:
            return _json_dumps(self._read_google_doc_with_token(docs_service, document_id))
        except HttpError as e:
            return f"Error reading Google Doc: {e}"

    async def append_to_google_doc(
        self,
        document_id: str,
        content: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Append text to the end of a Google Doc."""
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, _ = await self._get_services(tool_context)
        if docs_service is None:
            return ""

        try:
            return _json_dumps(
                self._append_to_google_doc_with_token(docs_service, document_id, content)
            )
        except HttpError as e:
            return f"Error appending to Google Doc: {e}"

    async def replace_text_in_google_doc(
        self,
        document_id: str,
        search_text: str,
        replace_text: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Replace matching text throughout a Google Doc."""
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, _ = await self._get_services(tool_context)
        if docs_service is None:
            return ""

        try:
            return _json_dumps(
                self._replace_text_in_google_doc_with_token(
                    docs_service,
                    document_id,
                    search_text,
                    replace_text,
                )
            )
        except HttpError as e:
            return f"Error replacing text in Google Doc: {e}"

    async def get_docs_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """Check if Google Docs is connected."""
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, _ = await self._get_services(tool_context)
        if docs_service is None:
            return ""
        return "Google Workspace (Drive, Sheets, Docs) is connected."


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


def _row_to_record(headers: List[str], row: List[Any]) -> Dict[str, str]:
    record: Dict[str, str] = {}
    for index, header in enumerate(headers):
        value = row[index] if index < len(row) else ""
        record[header] = _stringify_cell(value)
    return record


def _stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _json_dumps(obj: dict) -> str:
    import json
    return json.dumps(obj, ensure_ascii=True)
