"""Email tool for the Schoopet agent.

Provides on-demand email reading, full email fetching with attachment handling,
and sender authorization management.

Uses the system workspace_system token (phone="email_system", feature="workspace_system")
to read the shared inbox, filtered per-user by the email_authorizations Firestore
collection.
"""
import base64
import logging
import os
from typing import Optional

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# System Gmail account identifiers (mirror of gmail_client.py constants)
SYSTEM_PHONE = "email_system"
SYSTEM_FEATURE = "workspace_system"

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
AUTHORIZATIONS_COLLECTION = "email_workflows"

# MIME types natively understood by Gemini as inline_data
GEMINI_SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    # Documents
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/tiff",
    # Audio
    "audio/wav",
    "audio/mp3",
    "audio/mpeg",
    "audio/aiff",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
    # Video
    "video/mp4",
    "video/mpeg",
    "video/mov",
    "video/avi",
    "video/x-flv",
    "video/mpg",
    "video/webm",
    "video/wmv",
    "video/3gpp",
})


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
        """Fetch a single message with full body and attachment parts."""
        import httpx
        with httpx.Client() as client:
            resp = client.get(
                f"{GMAIL_API_BASE}/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "full"},
            )
            resp.raise_for_status()
            return resp.json()

    def _gmail_fetch_metadata(self, message_id: str, token: str) -> Optional[dict]:
        """Fetch a single message with metadata headers only (lighter)."""
        import httpx
        with httpx.Client() as client:
            resp = client.get(
                f"{GMAIL_API_BASE}/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _decode_body(part: dict) -> str:
        """Decode base64url-encoded message body."""
        data = part.get("body", {}).get("data", "")
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    @classmethod
    def _extract_text(cls, payload: dict) -> str:
        """Recursively extract text/plain body from MIME payload."""
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/plain":
            return cls._decode_body(payload)
        if mime_type.startswith("multipart/"):
            for part in payload.get("parts", []):
                text = cls._extract_text(part)
                if text:
                    return text
        return ""

    def _parse_message(self, msg: dict) -> dict:
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        def get_header(name):
            for h in headers:
                if h.get("name", "").lower() == name.lower():
                    return h.get("value", "")
            return ""

        return {
            "id": msg.get("id", ""),
            "from": get_header("From"),
            "subject": get_header("Subject"),
            "date": get_header("Date"),
            "body": self._extract_text(payload),
            "snippet": msg.get("snippet", ""),
        }

    def _collect_attachments(
        self, payload: dict, message_id: str, token: str
    ) -> list[dict]:
        """Recursively walk the MIME tree and collect attachment data.

        For each attachment part:
        - If the MIME type is in GEMINI_SUPPORTED_MIME_TYPES: fetch bytes and
          return {"filename", "mime_type", "bytes": bytes}.
        - Otherwise: return {"filename", "mime_type", "bytes": None} so the caller
          can emit a text note about the unsupported attachment.

        text/plain parts are skipped (handled as the email body).
        """
        import httpx
        results: list[dict] = []
        with httpx.Client() as client:
            self._walk_parts(payload, message_id, token, client, results)
        return results

    def _walk_parts(
        self, part: dict, message_id: str, token: str, client, results: list
    ) -> None:
        mime_type = part.get("mimeType", "")

        # Skip inline text body parts (plain, html, calendar, etc.) — no filename means body content
        if mime_type.startswith("text/") and not part.get("filename", ""):
            return

        # Recurse into multipart containers
        if mime_type.startswith("multipart/"):
            for sub in part.get("parts", []):
                self._walk_parts(sub, message_id, token, client, results)
            return

        # Skip text/html and other inline text variants without a filename
        filename = part.get("filename", "")
        body = part.get("body", {})
        if not filename and not body.get("attachmentId") and not body.get("data"):
            return

        # Determine bytes for supported types; None for unsupported
        attachment_bytes: Optional[bytes] = None
        if mime_type in GEMINI_SUPPORTED_MIME_TYPES:
            attachment_bytes = self._fetch_attachment_bytes(part, message_id, token, client)

        results.append(
            {
                "filename": filename or "(unnamed)",
                "mime_type": mime_type,
                "bytes": attachment_bytes,
            }
        )

    def _fetch_attachment_bytes(
        self, part: dict, message_id: str, token: str, client
    ) -> Optional[bytes]:
        """Decode or download attachment bytes for a single MIME part."""
        body = part.get("body", {})

        # Small/inline attachment: data is embedded in the payload
        if body.get("data"):
            try:
                return base64.urlsafe_b64decode(body["data"] + "==")
            except Exception as e:
                logger.warning(f"Failed to decode inline attachment data: {e}")
                return None

        # Large attachment: fetch via attachments.get
        attachment_id = body.get("attachmentId")
        if not attachment_id:
            return None

        try:
            resp = client.get(
                f"{GMAIL_API_BASE}/messages/{message_id}/attachments/{attachment_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==")
        except Exception as e:
            logger.warning(f"Failed to fetch attachment {attachment_id}: {e}")

        return None

    # ── Public tool methods ────────────────────────────────────────────────

    def read_emails(
        self,
        query: str = "",
        max_results: int = 10,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Read emails from the shared inbox filtered to authorized senders only.

        Returns a list with message IDs, sender, subject, and a preview snippet.
        Call fetch_email(message_id) to get the full content and attachments for
        a specific message.

        Args:
            query: Additional Gmail search query (e.g., "is:unread").
            max_results: Maximum number of emails to return (default: 10).

        Returns:
            Formatted email list with IDs, or a message if no emails found.

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
                raw = self._gmail_fetch_metadata(m["id"], token)
                if not raw:
                    continue
                headers_list = raw.get("payload", {}).get("headers", [])

                def gh(name):
                    for h in headers_list:
                        if h.get("name", "").lower() == name.lower():
                            return h.get("value", "")
                    return ""

                snippet = raw.get("snippet", "")[:200].replace("\n", " ")
                results.append(
                    f"ID: {m['id']}\n"
                    f"From: {gh('From')}\n"
                    f"Subject: {gh('Subject')}\n"
                    f"Date: {gh('Date')}\n"
                    f"Preview: {snippet}"
                )
            except Exception as e:
                results.append(f"[Error fetching message {m['id']}: {e}]")

        return (
            f"Found {len(results)} email(s):\n\n"
            + "\n\n---\n\n".join(results)
            + "\n\nCall fetch_email(message_id) for full content and attachments."
        )

    async def fetch_email(
        self,
        message_id: str,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Fetch a single email by message ID, store any supported attachments
        in the artifact registry, and return the full formatted content.

        Use this after read_emails identifies a message to process. The returned
        text includes an 'Attachments' section listing artifact keys you can pass
        to save_attachment_to_drive.

        Attachments in supported formats (PDF, DOCX, images, audio, video) are
        stored as artifacts and can be accessed by Gemini for content extraction.

        Args:
            message_id: Gmail message ID (from read_emails output).

        Returns:
            Formatted email with body and attachment artifact keys,
            or an error message.
        """
        token = self._get_system_token()
        if not token:
            return (
                "The email system is not connected yet. "
                "Please ask an admin to set up the system Gmail authorization."
            )

        try:
            raw = self._gmail_fetch(message_id, token)
        except Exception as e:
            return f"Error fetching email {message_id}: {e}"

        parsed = self._parse_message(raw)
        payload = raw.get("payload", {})
        attachments = self._collect_attachments(payload, message_id, token)

        artifact_keys: list[tuple[str, str]] = []
        unsupported_files: list[str] = []

        for a in attachments:
            artifact_key = f"{message_id}_{a['filename']}"
            if a.get("bytes") is not None:
                if tool_context is not None:
                    try:
                        from google.genai import types
                        await tool_context.save_artifact(
                            artifact_key,
                            types.Part(
                                inline_data=types.Blob(
                                    mime_type=a["mime_type"],
                                    data=a["bytes"],
                                )
                            ),
                        )
                        artifact_keys.append((artifact_key, a["mime_type"]))
                    except Exception as e:
                        logger.warning(f"Failed to save artifact {artifact_key}: {e}")
            else:
                unsupported_files.append(a["filename"])

        body = parsed.get("body") or parsed.get("snippet", "")
        lines = [
            f"From: {parsed['from']}",
            f"Subject: {parsed['subject']}",
            f"Date: {parsed['date']}",
            "",
            body,
        ]

        if artifact_keys or unsupported_files:
            lines.append("")
            lines.append("Attachments (use save_attachment_to_drive to save binary to Drive):")
            for key, mime in artifact_keys:
                lines.append(f'  artifact: "{key}" ({mime})')
            for fname in unsupported_files:
                lines.append(f"  [{fname} — type not supported, binary not available]")

        return "\n".join(lines)

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

    async def list_artifacts(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        List all artifacts stored in the current session's artifact registry.

        Artifacts are saved automatically by fetch_email for each supported attachment
        (PDF, DOCX, images, etc.). Each key has the format "<message_id>_<filename>".
        Pass a key to read_artifact to load and analyze the file.

        Returns:
            Formatted list of artifact keys with MIME type and size,
            or a message if none are stored.
        """
        if tool_context is None:
            return "ERROR: No tool_context available."
        try:
            keys = await tool_context.list_artifacts()
        except Exception as e:
            return f"Error listing artifacts: {e}"
        if not keys:
            return "No artifacts stored in this session."
        lines = [f"Stored artifacts ({len(keys)}):"]
        for key in keys:
            try:
                part = await tool_context.load_artifact(key)
                mime = part.inline_data.mime_type if part and part.inline_data else "unknown"
                size = len(part.inline_data.data) if part and part.inline_data else 0
                lines.append(f'  "{key}"  ({mime}, {size:,} bytes)')
            except Exception:
                lines.append(f'  "{key}"  (metadata unavailable)')
        return "\n".join(lines)

    async def read_artifact(
        self,
        artifact_key: str,
        tool_context: ToolContext = None,
    ) -> dict:
        """
        Load an artifact from the session registry and make it available to the model.

        The artifact bytes (PDF, image, etc.) are injected directly into the model's
        context as inline_data by the agent's before_model_callback, so the model can
        perform native multimodal analysis (e.g., extract text from a PDF, describe an image).

        For plain text artifacts the decoded content is also returned in this response.

        Args:
            artifact_key: Artifact key from list_artifacts or fetch_email output
                          (format: "<message_id>_<filename>").

        Returns:
            Dict with status, artifact metadata, and tool_response_artifact_id so the
            before_model_callback knows which artifact to inject.
        """
        if tool_context is None:
            return {"status": "error", "message": "No tool_context available.",
                    "tool_response_artifact_id": ""}
        try:
            part = await tool_context.load_artifact(artifact_key)
        except Exception as e:
            return {"status": "error", "message": str(e), "tool_response_artifact_id": ""}
        if part is None or part.inline_data is None:
            return {"status": "error",
                    "message": f"Artifact '{artifact_key}' not found.",
                    "tool_response_artifact_id": ""}
        mime = part.inline_data.mime_type
        size = len(part.inline_data.data)
        result = {
            "status": "success",
            "artifact_key": artifact_key,
            "mime_type": mime,
            "size_bytes": size,
            "tool_response_artifact_id": artifact_key,
        }
        if mime.startswith("text/"):
            result["content"] = part.inline_data.data.decode("utf-8", errors="replace")
        return result

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
                "An admin needs to authorize the workspace_system OAuth token. "
                "Contact support to set this up."
            )
        return "System Gmail is connected and ready to receive emails."
