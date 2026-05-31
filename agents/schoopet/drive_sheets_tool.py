"""Google Drive and Google Sheets tools for the Schoopet agent.

Uses IAM connector credentials for the calling user's personal Google account.
"""
import logging
from typing import Optional, List, Dict, Any

from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .resource_confirmation import _RESOURCE_CONFIRMED_PREFIX
from .utils import require_user_id

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"


class DriveTool:
    """Tool for saving and listing files in Google Drive using IAM connector credentials."""

    async def _get_service(self, tool_context: ToolContext):
        from .gcp_auth import get_workspace_service
        return await get_workspace_service("drive", "v3", "drive", tool_context)

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
            folder_id: (Required) Drive folder ID. Use 'root' to list the Drive root folder.
                       Do not omit — the parameter is mandatory.
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

    async def _get_service(self, tool_context: ToolContext):
        from .gcp_auth import get_workspace_service
        return await get_workspace_service("sheets", "v4", "sheets", tool_context)

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

    def _get_sheet_gid(self, service, spreadsheet_id: str, tab_name: str) -> Optional[int]:
        """Return the numeric sheetId (gid) for a named tab, or None."""
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == tab_name:
                return props.get("sheetId")
        return None

    def _format_header_row(self, service, spreadsheet_id: str, tab_name: str, num_cols: int) -> None:
        """Bold and freeze the header row for a sheet tab."""
        gid = self._get_sheet_gid(service, spreadsheet_id, tab_name)
        if gid is None or num_cols == 0:
            return
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": gid,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": num_cols,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {"bold": True},
                                    "backgroundColor": {"red": 0.85, "green": 0.90, "blue": 0.98},
                                }
                            },
                            "fields": "userEnteredFormat(textFormat,backgroundColor)",
                        }
                    },
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": gid,
                                "gridProperties": {"frozenRowCount": 1},
                            },
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                ]
            },
        ).execute()

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

        self._format_header_row(service, sheet_id, sheet_tab, len(current_headers))

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

    def _batch_update_rows_with_token(
        self,
        service,
        sheet_id: str,
        rows: List[Dict[str, Any]],
        sheet_tab: str,
    ) -> Dict[str, Any]:
        headers = self._get_header_row(service, sheet_id, sheet_tab)
        if not headers:
            raise ValueError("Sheet has no header row.")

        for entry in rows:
            missing = [h for h in entry.get("updates", {}) if h not in headers]
            if missing:
                raise ValueError(f"Unknown columns in row {entry.get('row')}: {', '.join(missing)}")

        data = []
        for entry in rows:
            row_num = entry["row"]
            for header, value in entry.get("updates", {}).items():
                col_letter = _col_to_letter(headers.index(header) + 1)
                data.append({
                    "range": f"{sheet_tab}!{col_letter}{row_num}",
                    "values": [[_stringify_cell(value)]],
                })

        if not data:
            return {"sheet_id": sheet_id, "sheet_tab": sheet_tab, "updated_cells": 0, "rows_affected": 0}

        result = (
            service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=sheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            )
            .execute()
        )
        return {
            "sheet_id": sheet_id,
            "sheet_tab": sheet_tab,
            "updated_cells": result.get("totalUpdatedCells", len(data)),
            "rows_affected": len(rows),
        }

    # ── Public methods ──────────────────────────────────────────────────────────

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
            cell_range = f"{sheet_tab}!{_col_to_letter(column)}{row}"
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
            result = self._create_spreadsheet_with_token(
                service,
                title,
                sheet_tab,
                headers or [],
            )
            sheet_id = result.get("spreadsheet_id", "")
            if sheet_id and tool_context is not None:
                tool_context.state[f"{_RESOURCE_CONFIRMED_PREFIX}{sheet_id}"] = True
            return _json_dumps(result)
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

    async def batch_update_sheet_rows(
        self,
        sheet_id: str,
        rows: List[Dict[str, Any]],
        sheet_tab: str = "Sheet1",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Update multiple rows in a single API call with one confirmation.

        Use this instead of calling update_sheet_row repeatedly when updating
        many rows at once (e.g. bulk edits, rewrites, imports).

        Args:
            sheet_id: The Google Sheets document ID (from the URL).
            rows: List of row updates. Each entry must have:
                  - "row": 1-indexed row number (row 1 = header; data starts at 2)
                  - "updates": dict mapping column header names to new values
            sheet_tab: Name of the sheet tab (default: "Sheet1").

        Returns:
            JSON with updated_cells and rows_affected counts, or error message.

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
            return _json_dumps(
                self._batch_update_rows_with_token(service, sheet_id, rows, sheet_tab)
            )
        except (HttpError, ValueError) as e:
            return f"Error batch updating sheet rows: {e}"


class DocsTool:
    """Tool for creating and editing Google Docs using IAM connector credentials."""

    async def _get_services(self, tool_context: ToolContext):
        """Return (docs_service, drive_service) or (None, None) after emitting credential request."""
        from .gcp_auth import get_workspace_service
        docs_service = await get_workspace_service("docs", "v1", "docs", tool_context)
        if docs_service is None:
            return None, None
        drive_service = await get_workspace_service("drive", "v3", "docs/drive", tool_context)
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
            self._append_formatted_to_doc_with_token(docs_service, document_id, content)

        return {
            "document_id": document_id,
            "document_url": document_url,
            "title": title,
            "folder_id": folder_id,
        }

    def _extract_document_markdown(self, document: Dict[str, Any]) -> str:
        _HEADING_PREFIX = {
            "HEADING_1": "# ",
            "HEADING_2": "## ",
            "HEADING_3": "### ",
            "HEADING_4": "#### ",
        }
        lines: List[str] = []
        for element in document.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            style_type = paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
            prefix = _HEADING_PREFIX.get(style_type, "")
            is_bullet = "bullet" in paragraph
            runs: List[str] = []
            for item in paragraph.get("elements", []):
                text_run = item.get("textRun")
                if not text_run:
                    continue
                text = text_run.get("content", "").rstrip("\n")
                if not text:
                    continue
                ts = text_run.get("textStyle", {})
                bold = ts.get("bold", False)
                italic = ts.get("italic", False)
                if bold and italic:
                    text = f"***{text}***"
                elif bold:
                    text = f"**{text}**"
                elif italic:
                    text = f"*{text}*"
                runs.append(text)
            line = "".join(runs)
            if not line:
                lines.append("")
                continue
            if prefix:
                lines.append(f"{prefix}{line}")
            elif is_bullet:
                lines.append(f"- {line}")
            else:
                lines.append(line)
        return "\n".join(lines)

    def _read_google_doc_with_token(
        self,
        docs_service,
        document_id: str,
    ) -> Dict[str, Any]:
        document = docs_service.documents().get(documentId=document_id).execute()
        return {
            "document_id": document_id,
            "title": document.get("title", ""),
            "text": self._extract_document_markdown(document),
        }

    def _overwrite_google_doc_with_token(
        self,
        docs_service,
        document_id: str,
        content: str,
    ) -> Dict[str, Any]:
        document = docs_service.documents().get(documentId=document_id).execute()
        end_index = document.get("body", {}).get("content", [{}])[-1].get("endIndex", 2)
        requests: List[Dict] = []
        if end_index > 2:
            requests.append({
                "deleteContentRange": {
                    "range": {"startIndex": 1, "endIndex": end_index - 1}
                }
            })
        formatted, _ = _build_formatted_requests(content, 1)
        requests.extend(formatted)
        if requests:
            docs_service.documents().batchUpdate(
                documentId=document_id,
                body={"requests": requests},
            ).execute()
        return {
            "document_id": document_id,
            "document_url": f"https://docs.google.com/document/d/{document_id}/edit",
            "written_characters": len(content),
        }

    def _append_formatted_to_doc_with_token(
        self,
        docs_service,
        document_id: str,
        content: str,
    ) -> Dict[str, Any]:
        document = docs_service.documents().get(documentId=document_id).execute()
        end_index = max(1, document.get("body", {}).get("content", [{}])[-1].get("endIndex", 1) - 1)
        requests, _ = _build_formatted_requests(content, end_index)
        if requests:
            docs_service.documents().batchUpdate(
                documentId=document_id,
                body={"requests": requests},
            ).execute()
        return {
            "document_id": document_id,
            "document_url": f"https://docs.google.com/document/d/{document_id}/edit",
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
            result = self._create_google_doc_with_token(
                docs_service,
                drive_service,
                title,
                content,
                folder_id,
            )
            document_id = result.get("document_id", "")
            if document_id and tool_context is not None:
                tool_context.state[f"{_RESOURCE_CONFIRMED_PREFIX}{document_id}"] = True
            return _json_dumps(result)
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

    async def append_formatted_to_doc(
        self,
        document_id: str,
        content: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Append rich formatted content to a Google Doc using Markdown-like syntax.

        Supported formatting:
        - # Heading 1 / ## Heading 2 / ### Heading 3
        - **bold**, *italic*, ***bold italic***
        - - bullet item  (leading dash + space)
        - --- (horizontal divider — inserts a blank line)
        - Plain paragraphs (blank lines become empty paragraphs)
        """
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, _ = await self._get_services(tool_context)
        if docs_service is None:
            return ""

        try:
            return _json_dumps(
                self._append_formatted_to_doc_with_token(docs_service, document_id, content)
            )
        except HttpError as e:
            return f"Error appending formatted content to Google Doc: {e}"

    async def overwrite_google_doc(
        self,
        document_id: str,
        content: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Replace the entire content of a Google Doc with new formatted content.

        Clears the existing body and writes fresh content using the same Markdown
        syntax as append_formatted_to_doc. Use this for recurring tasks that want
        to regenerate the doc from scratch rather than append.
        """
        _, err = require_user_id(tool_context, "docs")
        if err:
            return err

        docs_service, _ = await self._get_services(tool_context)
        if docs_service is None:
            return ""

        try:
            return _json_dumps(
                self._overwrite_google_doc_with_token(docs_service, document_id, content)
            )
        except HttpError as e:
            return f"Error overwriting Google Doc: {e}"

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


def _utf16_len(s: str) -> int:
    """UTF-16 code units — what the Google Docs API counts for character indices."""
    return len(s.encode("utf-16-le")) // 2


def _parse_inline_runs(text: str) -> List[tuple]:
    """Parse inline markdown into list of (text, bold, italic) tuples."""
    import re
    runs: List[tuple] = []
    pattern = re.compile(r"\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*", re.DOTALL)
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            runs.append((text[last_end : m.start()], False, False))
        if m.group(1) is not None:
            runs.append((m.group(1), True, True))
        elif m.group(2) is not None:
            runs.append((m.group(2), True, False))
        else:
            runs.append((m.group(3), False, True))
        last_end = m.end()
    if last_end < len(text):
        runs.append((text[last_end:], False, False))
    return runs or [(text, False, False)]


def _build_formatted_requests(markdown: str, start_index: int) -> tuple:
    """Convert a markdown string into Google Docs batchUpdate requests.

    Handles: # H1 / ## H2 / ### H3, **bold**, *italic*, - bullets, --- dividers.
    Returns (requests_list, final_end_index).

    Requests are ordered so that insertText calls come first (in document order),
    followed by paragraph-style and inline-style updates, and finally bullet
    formatting — matching the sequence the Docs API executes them.
    """
    insert_requests: List[Dict] = []
    style_requests: List[Dict] = []
    bullet_requests: List[Dict] = []
    idx = start_index

    for line in markdown.split("\n"):
        is_bullet = False

        if line.startswith("### "):
            named_style, plain = "HEADING_3", line[4:]
        elif line.startswith("## "):
            named_style, plain = "HEADING_2", line[3:]
        elif line.startswith("# "):
            named_style, plain = "HEADING_1", line[2:]
        elif line.startswith("- ") or line.startswith("* "):
            named_style, plain, is_bullet = "NORMAL_TEXT", line[2:], True
        elif line == "---":
            insert_requests.append(
                {"insertText": {"location": {"index": idx}, "text": "\n"}}
            )
            idx += 1
            continue
        else:
            named_style, plain = "NORMAL_TEXT", line

        inline_runs = _parse_inline_runs(plain)
        paragraph_text = "".join(r[0] for r in inline_runs) + "\n"
        para_len = _utf16_len(paragraph_text)
        para_end = idx + para_len

        insert_requests.append(
            {"insertText": {"location": {"index": idx}, "text": paragraph_text}}
        )

        if named_style != "NORMAL_TEXT":
            style_requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": idx, "endIndex": para_end},
                        "paragraphStyle": {"namedStyleType": named_style},
                        "fields": "namedStyleType",
                    }
                }
            )

        if is_bullet:
            bullet_requests.append(
                {
                    "createParagraphBullets": {
                        "range": {"startIndex": idx, "endIndex": para_end},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )

        run_idx = idx
        for run_text, bold, italic in inline_runs:
            run_end = run_idx + _utf16_len(run_text)
            if bold or italic:
                text_style: Dict[str, Any] = {}
                fields: List[str] = []
                if bold:
                    text_style["bold"] = True
                    fields.append("bold")
                if italic:
                    text_style["italic"] = True
                    fields.append("italic")
                style_requests.append(
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": run_idx, "endIndex": run_end},
                            "textStyle": text_style,
                            "fields": ",".join(fields),
                        }
                    }
                )
            run_idx = run_end

        idx = para_end

    return insert_requests + style_requests + bullet_requests, idx
