import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import typing

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

class GoogleSheetsTool:
    def __init__(self):
        self.creds = None
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Shows basic usage of the Sheets API.
        Prints the names and majors of students in a sample spreadsheet.
        """
        if os.path.exists("token.json"):
            self.creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                if os.path.exists("credentials.json"):
                     flow = InstalledAppFlow.from_client_secrets_file(
                        "credentials.json", SCOPES
                    )
                     self.creds = flow.run_local_server(port=0)
                else:
                    print("Warning: credentials.json not found. Sheets tool will fail if used.")
                    return

            # Save the credentials for the next run
            with open("token.json", "w") as token:
                token.write(self.creds.to_json())

        try:
            self.service = build("sheets", "v4", credentials=self.creds)
        except HttpError as err:
            print(err)

    def read_sheet(self, spreadsheet_id: str, range_name: str) -> str:
        """
        Reads values from a Google Sheet.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet.
            range_name: The range to read, e.g., 'Sheet1!A1:E'.
            
        Returns:
            A string representation of the rows.
        """
        if not self.service:
            return "Error: Sheets service not initialized (missing credentials)."
            
        try:
            sheet = self.service.spreadsheets()
            result = (
                sheet.values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
            values = result.get("values", [])

            if not values:
                return "No data found."

            return str(values)
        except HttpError as err:
            return f"API Error: {err}"

    def write_sheet(self, spreadsheet_id: str, range_name: str, values: typing.List[typing.List[str]]) -> str:
        """
        Writes values to a Google Sheet.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet.
            range_name: The range to write to.
            values: A list of lists of strings to write.
            
        Returns:
            Status message.
        """
        if not self.service:
            return "Error: Sheets service not initialized (missing credentials)."

        try:
            body = {"values": values}
            result = (
                self.service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body=body,
                )
                .execute()
            )
            return f"{result.get('updatedCells')} cells updated."
        except HttpError as err:
            return f"API Error: {err}"
