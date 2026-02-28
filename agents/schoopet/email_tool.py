"""Email tool for the Schoopet agent.

Provides on-demand email reading and sender authorization management.
Uses the system gmail_system token (phone="email_system", feature="gmail_system")
to read the shared inbox, filtered per-user by the email_authorizations Firestore
collection.
"""
import logging
import os
import base64
from typing import Optional

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# System Gmail account identifiers (mirror of gmail_client.py constants)
SYSTEM_PHONE = "email_system"
SYSTEM_FEATURE = "gmail_system"

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
AUTHORIZATIONS_COLLECTION = "email_workflows"


class EmailTool:
    """Agent tool for email reading and authorization management.

    Uses:
    - OAuthClient (with system account) to read Gmail
    - Firestore directly for email_authorizations lookups
    """

    def __init__(self):
        self._oauth_client = None
        self._firestore_client = None

    def _get_oauth_client(self):
        if self._oauth_client is None:
            from .oauth_client import OAuthClient
            self._oauth_client = OAuthClient()
        return self._oauth_client

    def _get_firestore(self):
        if self._firestore_client is None:
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if project:
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=project)
        return self._firestore_client

    # ── Firestore helpers ──────────────────────────────────────────────────

    def _get_authorized_senders(self, phone: str) -> list[str]:
        """Return list of sender emails authorized for this phone."""
        db = self._get_firestore()
        if not db:
            return []
        try:
            docs = (
                db.collection(AUTHORIZATIONS_COLLECTION)
                .where("authorized_user_phone", "==", phone)
                .stream()
            )
            return [doc.to_dict().get("sender_email", "") for doc in docs]
        except Exception as e:
            logger.error(f"Failed to load authorized senders: {e}")
            return []

    def _get_authorization(self, sender_email: str) -> Optional[dict]:
        db = self._get_firestore()
        if not db:
            return None
        try:
            doc = (
                db.collection(AUTHORIZATIONS_COLLECTION)
                .document(sender_email.lower().strip())
                .get()
            )
            return doc.to_dict() if doc.exists else None
        except Exception:
            return None

    # ── Gmail helpers ──────────────────────────────────────────────────────

    def _get_system_token(self) -> Optional[str]:
        return self._get_oauth_client().get_valid_access_token(SYSTEM_PHONE, SYSTEM_FEATURE)

    def _gmail_search(self, query: str, max_results: int, token: str) -> list[dict]:
        import httpx
        with httpx.Client() as client:
            resp = client.get(
                f"{GMAIL_API_BASE}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "maxResults": max_results},
            )
            resp.raise_for_status()
            return resp.json().get("messages", [])

    def _gmail_fetch(self, message_id: str, token: str) -> Optional[dict]:
        import httpx
        with httpx.Client() as client:
            resp = client.get(
                f"{GMAIL_API_BASE}/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "full"},
            )
            resp.raise_for_status()
            return resp.json()

    def _parse_message(self, msg: dict) -> dict:
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        def get_header(name):
            for h in headers:
                if h.get("name", "").lower() == name.lower():
                    return h.get("value", "")
            return ""

        def decode_body(part):
            data = part.get("body", {}).get("data", "")
            if not data:
                return ""
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""

        def extract_text(p):
            mime = p.get("mimeType", "")
            if mime == "text/plain":
                return decode_body(p)
            if mime.startswith("multipart/"):
                for part in p.get("parts", []):
                    text = extract_text(part)
                    if text:
                        return text
            return ""

        return {
            "id": msg.get("id", ""),
            "from": get_header("From"),
            "subject": get_header("Subject"),
            "date": get_header("Date"),
            "body": extract_text(payload),
            "snippet": msg.get("snippet", ""),
        }

    # ── Public tool methods ────────────────────────────────────────────────

    def read_emails(
        self,
        query: str = "",
        max_results: int = 10,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Read emails from the shared inbox filtered to authorized senders only.

        Args:
            query: Additional Gmail search query (e.g., "is:unread").
            max_results: Maximum number of emails to return (default: 10).

        Returns:
            Formatted email list, or a message if no emails found.

        Note:
            Requires user_id from tool_context (phone number).
            Only returns emails from senders the user has explicitly authorized.
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot read emails — no user_id in tool_context."

        phone = tool_context.user_id
        token = self._get_system_token()
        if not token:
            return (
                "The email system is not connected yet. "
                "Please ask an admin to set up the system Gmail authorization."
            )

        senders = self._get_authorized_senders(phone)
        if not senders:
            return (
                "You haven't set up any email workflows yet. "
                "Use add_email_workflow(sender_email, processing_prompt) to add one."
            )

        sender_filter = " OR ".join(f"from:{s}" for s in senders)
        full_query = f"({sender_filter})"
        if query:
            full_query = f"{full_query} {query}"

        try:
            messages = self._gmail_search(full_query, max_results, token)
        except Exception as e:
            return f"Error searching emails: {e}"

        if not messages:
            return "No emails found from your authorized senders."

        results = []
        for m in messages:
            try:
                raw = self._gmail_fetch(m["id"], token)
                parsed = self._parse_message(raw)
                body_preview = (parsed["body"] or parsed["snippet"])[:200].replace("\n", " ")
                results.append(
                    f"From: {parsed['from']}\n"
                    f"Subject: {parsed['subject']}\n"
                    f"Date: {parsed['date']}\n"
                    f"Preview: {body_preview}"
                )
            except Exception as e:
                results.append(f"[Error fetching message {m['id']}: {e}]")

        return f"Found {len(results)} email(s):\n\n" + "\n\n---\n\n".join(results)

    def add_email_workflow(
        self,
        sender_email: str,
        processing_prompt: str,
        drive_folder_id: str = "",
        sheet_id: str = "",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Register an email sender with a custom processing workflow.

        When an email arrives from this sender it will be routed to you and the
        agent will execute the instructions in processing_prompt automatically.

        Args:
            sender_email: The sender's email address to authorize.
            processing_prompt: Instructions for what to do with each email
                (e.g. "Extract invoice numbers and save to Drive").
            drive_folder_id: Optional Google Drive folder ID for saving emails.
            sheet_id: Optional Google Sheets ID for logging emails.

        Returns:
            Confirmation message, or Drive/Sheets auth links if tokens are missing.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot add workflow — no user_id in tool_context."

        phone = tool_context.user_id
        db = self._get_firestore()
        if not db:
            return "ERROR: Database not available."

        try:
            doc_id = sender_email.lower().strip()
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id).set(
                {
                    "sender_email": doc_id,
                    "authorized_user_phone": phone,
                    "drive_folder_id": drive_folder_id,
                    "sheet_id": sheet_id,
                    "processing_prompt": processing_prompt,
                    "updated_at": now,
                    "created_at": now,
                },
                merge=True,
            )
        except Exception as e:
            return f"Failed to save workflow: {e}"

        messages = [
            f"Workflow created for {sender_email} — emails will be routed to you and processed automatically."
        ]

        # Check Google Workspace token (covers both Drive and Sheets)
        oauth = self._get_oauth_client()
        ws_token = oauth.get_valid_access_token(phone, "google-workspace")
        if not ws_token:
            ws_link = oauth.get_oauth_link(phone, "google-workspace")
            messages.append(
                f"Google Workspace (Drive + Sheets) is not connected. Authorize here:\n{ws_link}"
            )

        return "\n\n".join(messages)

    def update_email_workflow(
        self,
        sender_email: str,
        processing_prompt: str = None,
        drive_folder_id: str = None,
        sheet_id: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Update an existing email workflow (patch-style — only provided fields are changed).

        Args:
            sender_email: The sender's email address whose workflow to update.
            processing_prompt: New instructions for processing emails (optional).
            drive_folder_id: New Drive folder ID (optional).
            sheet_id: New Sheets ID (optional).

        Returns:
            Confirmation or error message.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot update workflow — no user_id in tool_context."

        phone = tool_context.user_id
        db = self._get_firestore()
        if not db:
            return "ERROR: Database not available."

        doc_id = sender_email.lower().strip()
        try:
            doc_ref = db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id)
            doc = doc_ref.get()
            if not doc.exists:
                return f"No workflow found for {sender_email}. Use add_email_workflow() to create one."
            data = doc.to_dict()
            if data.get("authorized_user_phone") != phone:
                return f"You don't own the workflow for {sender_email}."

            updates = {}
            if processing_prompt is not None:
                updates["processing_prompt"] = processing_prompt
            if drive_folder_id is not None:
                updates["drive_folder_id"] = drive_folder_id
            if sheet_id is not None:
                updates["sheet_id"] = sheet_id

            if not updates:
                return "Nothing to update — provide at least one field to change."

            from datetime import datetime, timezone
            updates["updated_at"] = datetime.now(timezone.utc)
            doc_ref.update(updates)
        except Exception as e:
            return f"Failed to update workflow: {e}"

        return f"Workflow updated for {sender_email}."

    def remove_email_workflow(
        self,
        sender_email: str,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Remove the email workflow for a sender.

        Args:
            sender_email: The sender's email address whose workflow to remove.

        Returns:
            Confirmation or error message.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot remove workflow — no user_id in tool_context."

        phone = tool_context.user_id
        db = self._get_firestore()
        if not db:
            return "ERROR: Database not available."

        doc_id = sender_email.lower().strip()
        try:
            doc_ref = db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id)
            doc = doc_ref.get()
            if not doc.exists:
                return f"No workflow found for {sender_email}."
            data = doc.to_dict()
            if data.get("authorized_user_phone") != phone:
                return f"You don't own the workflow for {sender_email}."
            doc_ref.delete()
        except Exception as e:
            return f"Failed to remove workflow: {e}"

        return f"Workflow removed for {sender_email}. Subsequent emails from that sender will be discarded."

    def list_email_workflows(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        List all email workflows configured for this user.

        Returns:
            Formatted list of workflows with sender, prompt preview, and IDs.

        Note:
            Requires user_id from tool_context (phone number).
        """
        if not tool_context or not getattr(tool_context, "user_id", None):
            return "ERROR: Cannot list workflows — no user_id in tool_context."

        phone = tool_context.user_id
        senders = self._get_authorized_senders(phone)
        if not senders:
            return "No email workflows configured yet. Use add_email_workflow() to create one."

        lines = [f"Email workflows ({len(senders)}):"]
        for sender in senders:
            workflow = self._get_authorization(sender)
            if workflow:
                prompt_preview = (workflow.get("processing_prompt") or "")[:80]
                if len(workflow.get("processing_prompt") or "") > 80:
                    prompt_preview += "..."
                folder = workflow.get("drive_folder_id") or "not set"
                sheet = workflow.get("sheet_id") or "not set"
                lines.append(
                    f"  - {sender}\n"
                    f"    Prompt: {prompt_preview or '(default)'}\n"
                    f"    Drive folder: {folder} | Sheet: {sheet}"
                )
            else:
                lines.append(f"  - {sender}")
        return "\n".join(lines)

    def get_email_system_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check whether the system Gmail account is connected and ready.

        Returns:
            Status string indicating connection state.
        """
        token = self._get_system_token()
        if not token:
            return (
                "System Gmail is NOT connected. "
                "An admin needs to authorize the gmail_system OAuth token. "
                "Contact support to set this up."
            )
        return "System Gmail is connected and ready to receive emails."
