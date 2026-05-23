"""Email tool for the Schoopet agent.

Provides email reading, attachment handling, and email rule management.
Uses the calling user's personal Gmail inbox via their personal OAuth token.

Rules are stored in Firestore at email_rules/{user_id}/rules/{rule_id} for all users.
"""
import base64
import logging
import os
import uuid
from typing import Optional

from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import require_user_id

logger = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Unified email rules collection for all users.
# NOTE: sms-gateway/src/email/gmail_client.py defines the same constant — must stay in sync.
EMAIL_RULES_COLLECTION = "email_rules"

# MIME types natively understood by Gemini as inline_data
GEMINI_SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    # Documents — Gemini only supports PDF; DOCX and other Office formats are rejected
    "application/pdf",
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
    """Agent tool for email reading and rule management using IAM connector credentials."""

    def __init__(self):
        self._firestore_client = None

    def _get_firestore(self):
        if self._firestore_client is None:
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if project:
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=project)
        return self._firestore_client

    # ── Credential helpers ─────────────────────────────────────────────────

    async def _get_gmail_service(self, tool_context):
        from .gcp_auth import get_workspace_service
        return await get_workspace_service("gmail", "v1", "gmail", tool_context)

    # ── Firestore rule helpers ─────────────────────────────────────────────

    def _rules_ref(self, user_id: str):
        db = self._get_firestore()
        if not db:
            return None
        return (
            db.collection(EMAIL_RULES_COLLECTION)
            .document(user_id)
            .collection("rules")
        )

    def _get_rules(self, user_id: str) -> list[dict]:
        ref = self._rules_ref(user_id)
        if not ref:
            return []
        try:
            return [doc.to_dict() for doc in ref.stream()]
        except Exception as e:
            logger.exception(f"Failed to load email rules for {user_id[:4]}****")
            return []

    # ── Gmail helpers ──────────────────────────────────────────────────────

    def _gmail_search(self, service, query: str, max_results: int) -> list[dict]:
        logger.info(f"[gmail-api] messages.list → q={query!r} maxResults={max_results}")
        try:
            response = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
        except HttpError as e:
            logger.error(
                f"[gmail-api] messages.list failed: status={e.resp.status} "
                f"reason={e.reason!r} body={e.content[:300]!r}"
            )
            raise
        messages = response.get("messages", [])
        logger.info(
            f"[gmail-api] messages.list ok: {len(messages)} messages "
            f"resultSizeEstimate={response.get('resultSizeEstimate')}"
        )
        return messages

    def _gmail_history(self, service, since_history_id: str, max_results: int) -> list[dict]:
        """Return primary-inbox messages added since since_history_id via the History API."""
        logger.info(
            f"[gmail-api] history.list → startHistoryId={since_history_id!r} "
            f"maxResults={max_results}"
        )
        try:
            response = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=since_history_id,
                    historyTypes=["messageAdded"],
                    labelId="CATEGORY_PERSONAL",
                    maxResults=max_results,
                )
                .execute()
            )
        except HttpError as e:
            logger.error(
                f"[gmail-api] history.list failed: startHistoryId={since_history_id!r} "
                f"status={e.resp.status} reason={e.reason!r} body={e.content[:300]!r}"
            )
            raise
        seen = set()
        messages = []
        for record in response.get("history", []):
            for added in record.get("messagesAdded", []):
                msg = added.get("message", {})
                mid = msg.get("id", "")
                if mid and mid not in seen:
                    seen.add(mid)
                    messages.append({"id": mid})
        logger.info(
            f"[gmail-api] history.list ok: {len(messages)} new messages "
            f"historyId={response.get('historyId')}"
        )
        return messages

    def _gmail_fetch(self, service, message_id: str) -> Optional[dict]:
        """Fetch a single message with full body and attachment parts."""
        logger.info(f"[gmail-api] messages.get → id={message_id!r} format=full")
        try:
            result = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(
                    f"[gmail-api] messages.get not found (deleted/moved): id={message_id!r}"
                )
            else:
                logger.error(
                    f"[gmail-api] messages.get failed: id={message_id!r} "
                    f"status={e.resp.status} reason={e.reason!r} body={e.content[:300]!r}"
                )
            return None
        logger.info(
            f"[gmail-api] messages.get ok: id={message_id!r} "
            f"sizeEstimate={result.get('sizeEstimate')} labels={result.get('labelIds')}"
        )
        return result

    def _gmail_fetch_metadata(self, service, message_id: str) -> Optional[dict]:
        """Fetch a single message with metadata headers only (lighter)."""
        logger.info(f"[gmail-api] messages.get → id={message_id!r} format=metadata")
        try:
            result = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(
                    f"[gmail-api] messages.get(metadata) not found (deleted/moved): id={message_id!r}"
                )
            else:
                logger.error(
                    f"[gmail-api] messages.get(metadata) failed: id={message_id!r} "
                    f"status={e.resp.status} reason={e.reason!r} body={e.content[:300]!r}"
                )
            return None
        logger.info(f"[gmail-api] messages.get(metadata) ok: id={message_id!r}")
        return result

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
        self, payload: dict, message_id: str, service
    ) -> list[dict]:
        """Recursively walk the MIME tree and collect attachment data."""
        results: list[dict] = []
        self._walk_parts(payload, message_id, service, results)
        return results

    def _walk_parts(
        self, part: dict, message_id: str, service, results: list
    ) -> None:
        mime_type = part.get("mimeType", "")

        if mime_type.startswith("text/") and not part.get("filename", ""):
            return

        if mime_type.startswith("multipart/"):
            for sub in part.get("parts", []):
                self._walk_parts(sub, message_id, service, results)
            return

        filename = part.get("filename", "")
        body = part.get("body", {})
        if not filename and not body.get("attachmentId") and not body.get("data"):
            return

        attachment_bytes: Optional[bytes] = None
        if mime_type in GEMINI_SUPPORTED_MIME_TYPES:
            attachment_bytes = self._fetch_attachment_bytes(part, message_id, service)

        results.append(
            {
                "filename": filename or "(unnamed)",
                "mime_type": mime_type,
                "bytes": attachment_bytes,
            }
        )

    def _fetch_attachment_bytes(
        self, part: dict, message_id: str, service
    ) -> Optional[bytes]:
        """Decode or download attachment bytes for a single MIME part."""
        body = part.get("body", {})

        if body.get("data"):
            try:
                return base64.urlsafe_b64decode(body["data"] + "==")
            except Exception as e:
                logger.warning(f"Failed to decode inline attachment data: {e}", exc_info=True)
                return None

        attachment_id = body.get("attachmentId")
        if not attachment_id:
            return None

        try:
            response = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            data = response.get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==")
        except Exception as e:
            logger.warning(f"Failed to fetch attachment {attachment_id}: {e}", exc_info=True)

        return None

    # ── Public tool methods ────────────────────────────────────────────────

    async def read_emails(
        self,
        query: str = "",
        max_results: int = 10,
        since_history_id: str = "",
        tool_context: ToolContext = None,
    ) -> str:
        """
        Read emails from the inbox.

        Returns a list with message IDs, sender, subject, and a preview snippet.
        Call fetch_email(message_id) to get the full content and attachments.

        Args:
            query: Gmail search query (e.g., "is:unread", "from:boss@company.com").
                   Defaults to primary inbox only (excludes Promotions/Social/Updates tabs).
            max_results: Maximum number of emails to return (default: 10).
            since_history_id: If set, return only messages added since this Gmail
                              history ID (used for incremental notification processing).
        """
        _, err = require_user_id(tool_context, "email")
        if err:
            return err

        service = await self._get_gmail_service(tool_context)
        if not service:
            return ""

        try:
            if since_history_id:
                messages = self._gmail_history(service, since_history_id, max_results)
            else:
                full_query = query or "in:inbox category:primary"
                messages = self._gmail_search(service, full_query, max_results)
        except HttpError as e:
            logger.exception(f"[gmail-tool] read_emails error")
            return f"Error searching emails: {e}"
        except Exception as e:
            logger.exception(f"[gmail-tool] read_emails error")
            return f"Error searching emails: {e}"

        if not messages:
            return "No emails found."

        results = []
        for m in messages:
            try:
                raw = self._gmail_fetch_metadata(service, m["id"])
                if raw is None:
                    results.append(f"[Message {m['id']}: not found (deleted or moved to trash)]")
                    continue
                if "DRAFT" in (raw.get("labelIds") or []):
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
                logger.exception(f"[gmail-tool] error fetching message metadata {m['id']}")
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

        Use this after read_emails identifies a message to process, or when
        called from an INCOMING_EMAIL_NOTIFICATION prompt.

        Args:
            message_id: Gmail message ID (from read_emails output or notification).

        Returns:
            Formatted email with body and attachment artifact keys,
            or an error message.
        """
        _, err = require_user_id(tool_context, "email")
        if err:
            return err
        service = await self._get_gmail_service(tool_context)
        if not service:
            return ""

        raw = self._gmail_fetch(service, message_id)
        if raw is None:
            return f"Error fetching email {message_id}: message not found (deleted or moved to trash)"

        parsed = self._parse_message(raw)
        payload = raw.get("payload", {})
        attachments = self._collect_attachments(payload, message_id, service)

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
                        logger.warning(f"Failed to save artifact {artifact_key}: {e}", exc_info=True)
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

    async def list_artifacts(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        List all artifacts stored in the current session's artifact registry.

        Artifacts are saved automatically by fetch_email for each supported attachment
        (PDF, DOCX, images, etc.). Each key has the format "<message_id>_<filename>".
        """
        if tool_context is None:
            return "ERROR: No tool_context available."
        try:
            keys = await tool_context.list_artifacts()
        except Exception as e:
            logger.exception("[gmail-tool] list_artifacts error")
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
                logger.exception(f"[gmail-tool] artifact metadata error key={key}")
                lines.append(f'  "{key}"  (metadata unavailable)')
        return "\n".join(lines)

    async def read_artifact(
        self,
        artifact_key: str,
        tool_context: ToolContext = None,
    ) -> dict:
        """
        Load an artifact from the session registry and make it available to the model.

        Args:
            artifact_key: Artifact key from list_artifacts or fetch_email output.
        """
        if tool_context is None:
            return {"status": "error", "message": "No tool_context available.",
                    "tool_response_artifact_id": ""}
        try:
            part = await tool_context.load_artifact(artifact_key)
        except Exception as e:
            logger.exception(f"[gmail-tool] read_artifact error key={artifact_key}")
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

    async def atomic_read_received_emails(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Atomically claim the current batch of new emails and return their metadata.

        Called when an INCOMING_EMAIL_NOTIFICATION arrives. Reads the
        last_processed_id → last_received_id range from Firestore in a transaction,
        advances last_processed_id, clears pending_task_id, then fetches message
        metadata from the Gmail History API.

        Returns a formatted list of new emails, or "No new emails." if nothing is new.
        Call fetch_email(message_id) for full content and attachments.
        """
        _, err = require_user_id(tool_context, "email")
        if err:
            return err

        user_id = tool_context.user_id
        db = self._get_firestore()
        if not db:
            return "ERROR: Database not available."

        docs = list(
            db.collection("email_state")
            .where("user_id", "==", user_id)
            .limit(1)
            .stream()
        )
        if not docs:
            return "No email account connected."

        doc_ref = docs[0].reference
        old_processed_id = None
        claimed_received_id = None

        from google.cloud import firestore as _fs

        @_fs.transactional
        def _claim(transaction):
            nonlocal old_processed_id, claimed_received_id
            snap = doc_ref.get(transaction=transaction)
            d = snap.to_dict() if snap.exists else {}
            last_processed = d.get("last_processed_id") or d.get("last_history_id")
            last_received = d.get("last_received_id") or d.get("last_history_id")
            if not last_received or last_processed == last_received:
                return False
            old_processed_id = last_processed
            claimed_received_id = last_received
            transaction.update(doc_ref, {
                "last_processed_id": last_received,
                "pending_task_id": _fs.DELETE_FIELD,
            })
            return True

        try:
            claimed = _claim(db.transaction())
        except Exception as e:
            logger.exception("[gmail-tool] atomic_read_received_emails transaction error")
            return f"Error reading emails: {e}"

        if not claimed:
            return "No new emails."

        logger.info(
            "[gmail-tool] atomic_read user=%s first_id=%s last_id=%s",
            user_id, old_processed_id, claimed_received_id,
        )

        return await self.read_emails(
            since_history_id=old_processed_id,
            max_results=50,
            tool_context=tool_context,
        )

    # ── Unified email rule tools ───────────────────────────────────────────

    async def get_gmail_status(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Check whether Gmail is connected.

        Checks the current user's Gmail connection.

        Returns:
            Status string. If not connected, emits a credential request.
        """
        _, err = require_user_id(tool_context, "gmail status")
        if err:
            return err
        service = await self._get_gmail_service(tool_context)
        if service is None:
            return ""
        return "Your Gmail is connected. I'm monitoring your inbox for new emails."

    async def setup_gmail_watch(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Register Gmail push notifications for new emails.

        Sets up a Gmail push watch so the system automatically processes new
        emails as they arrive. Call this once to enable email monitoring, and
        again every ~7 days to keep the watch active.
        """
        _, err = require_user_id(tool_context, "gmail watch")
        if err:
            return err

        user_id = tool_context.user_id
        service = await self._get_gmail_service(tool_context)
        if not service:
            return ""

        topic = os.getenv("EMAIL_PUBSUB_TOPIC", "")
        if not topic:
            return "ERROR: EMAIL_PUBSUB_TOPIC is not configured."

        try:
            logger.info("[gmail-api] users.getProfile → userId=me")
            profile = service.users().getProfile(userId="me").execute()
            gmail_address = profile.get("emailAddress", "")
            logger.info(f"[gmail-api] users.getProfile ok: emailAddress={gmail_address!r}")
            if not gmail_address:
                return "ERROR: Could not determine Gmail address."

            logger.info(f"[gmail-api] users.watch → topicName={topic!r}")
            result = service.users().watch(
                userId="me",
                body={"labelIds": ["CATEGORY_PERSONAL"], "topicName": topic},
            ).execute()
            logger.info(
                f"[gmail-api] users.watch ok: historyId={result.get('historyId')} "
                f"expiration={result.get('expiration')}"
            )

            history_id = str(result.get("historyId", ""))
            expiration = result.get("expiration", "")

            db = self._get_firestore()
            if db:
                from datetime import datetime, timezone
                doc_id = gmail_address.lower().replace("@", "_at_").replace(".", "_")
                db.collection("email_state").document(doc_id).set(
                    {
                        "gmail_address": gmail_address,
                        "user_id": user_id,
                        "last_history_id": history_id,
                        "watch_expiration": expiration,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    merge=True,
                )

            return f"Gmail watch registered for {gmail_address}. historyId={history_id}"
        except HttpError as e:
            logger.exception("[gmail-tool] setup_gmail_watch HttpError")
            return f"Error setting up Gmail watch: {e}"
        except Exception as e:
            logger.exception("[gmail-tool] setup_gmail_watch error")
            return f"Error setting up Gmail watch: {e}"

    def add_email_rule(
        self,
        prompt: str,
        topic: str = "",
        sender_filter: str = "",
        target_channel_id: Optional[str] = None,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Add an email rule. Rules determine what the agent does when a matching
        email arrives in the inbox.

        Args:
            prompt: Instructions for what to do when a matching email arrives.
                    Written in natural language, e.g.:
                    - "Extract the applicant's name, email, and role. Log to sheet
                      <sheet_id> and send me a one-line summary."
                    - "Silently ignore — do not notify me."
                    - "Create a calendar event from the booking details and notify me."
            topic: Natural language description of the emails this rule matches
                   (e.g., "job applications", "flight confirmations", "invoices").
            sender_filter: Optional email address or domain to match
                           (e.g., "boss@company.com", "@stripe.com").
            target_channel_id: Optional Discord channel snowflake ID. When set,
                                summaries for this rule are routed exclusively to
                                that channel instead of the user's default DM.
                                Use list_discord_channels() to look up channel IDs.

        Returns:
            Confirmation message with the new rule ID.

        Note:
            At least one of `topic` or `sender_filter` must be provided.
        """
        phone, err = require_user_id(tool_context, "email rule")
        if err:
            return err

        if not topic and not sender_filter:
            return "ERROR: Provide at least a topic or sender_filter for the rule."

        if not prompt:
            return "ERROR: prompt is required — describe what to do when a matching email arrives."

        user_id = phone
        rules_ref = self._rules_ref(user_id)
        if not rules_ref:
            return "ERROR: Database not available."

        rule_id = str(uuid.uuid4())[:8]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        rule = {
            "rule_id": rule_id,
            "topic": topic,
            "sender_filter": sender_filter,
            "prompt": prompt,
            "created_at": now,
            "updated_at": now,
        }
        if target_channel_id:
            rule["target_channel_id"] = target_channel_id

        try:
            rules_ref.document(rule_id).set(rule)
        except Exception as e:
            logger.exception("[gmail-tool] add_email_rule error")
            return f"Failed to save rule: {e}"

        parts = []
        if topic:
            parts.append(f"topic: {topic}")
        if sender_filter:
            parts.append(f"sender: {sender_filter}")
        channel_note = f", routed to channel {target_channel_id}" if target_channel_id else ""
        return (
            f"Rule added (ID: {rule_id}) — "
            f"match [{', '.join(parts)}] → \"{prompt}\"{channel_note}."
        )

    def update_email_rule(
        self,
        rule_id: str,
        prompt: str = None,
        topic: str = None,
        sender_filter: str = None,
        target_channel_id: Optional[str] = None,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Update an existing email rule (patch-style — only provided fields change).

        Args:
            rule_id: Rule ID from list_email_rules.
            prompt: New instruction prompt (optional).
            topic: New topic description (optional).
            sender_filter: New sender filter (optional).
            target_channel_id: New Discord channel ID for routing (optional).
                                Pass an empty string "" to clear the channel routing.
        """
        phone, err = require_user_id(tool_context, "email rule")
        if err:
            return err

        user_id = phone
        rules_ref = self._rules_ref(user_id)
        if not rules_ref:
            return "ERROR: Database not available."

        try:
            doc_ref = rules_ref.document(rule_id)
            doc = doc_ref.get()
            if not doc.exists:
                return f"No rule found with ID {rule_id}. Use list_email_rules() to see your rules."

            updates = {}
            if prompt is not None:
                updates["prompt"] = prompt
            if topic is not None:
                updates["topic"] = topic
            if sender_filter is not None:
                updates["sender_filter"] = sender_filter
            if target_channel_id is not None:
                if target_channel_id == "":
                    from google.cloud.firestore_v1 import DELETE_FIELD
                    updates["target_channel_id"] = DELETE_FIELD
                else:
                    updates["target_channel_id"] = target_channel_id

            if not updates:
                return "Nothing to update — provide at least one field to change."

            from datetime import datetime, timezone
            updates["updated_at"] = datetime.now(timezone.utc)
            doc_ref.update(updates)
        except Exception as e:
            logger.exception("[gmail-tool] update_email_rule error")
            return f"Failed to update rule: {e}"

        return f"Rule {rule_id} updated."

    def remove_email_rule(
        self,
        rule_id: str,
        tool_context: ToolContext = None,
    ) -> str:
        """
        Remove an email rule.

        Args:
            rule_id: Rule ID from list_email_rules.
        """
        phone, err = require_user_id(tool_context, "email rule")
        if err:
            return err

        user_id = phone
        rules_ref = self._rules_ref(user_id)
        if not rules_ref:
            return "ERROR: Database not available."

        try:
            doc_ref = rules_ref.document(rule_id)
            doc = doc_ref.get()
            if not doc.exists:
                return f"No rule found with ID {rule_id}."
            doc_ref.delete()
        except Exception as e:
            logger.exception("[gmail-tool] remove_email_rule error")
            return f"Failed to remove rule: {e}"

        return f"Rule {rule_id} removed."

    def list_email_rules(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        List all email rules.

        Returns:
            Formatted list of rules with IDs, match criteria, and actions.
        """
        phone, err = require_user_id(tool_context, "email rule")
        if err:
            return err

        user_id = phone
        rules = self._get_rules(user_id)
        if not rules:
            return (
                "No email rules configured yet. "
                "Use add_email_rule() to set one up. "
                "Example: 'notify me about job applications and log them to a spreadsheet'."
            )

        lines = [f"Email rules ({len(rules)}):"]
        for r in rules:
            parts = []
            if r.get("topic"):
                parts.append(f"topic: {r['topic']}")
            if r.get("sender_filter"):
                parts.append(f"sender: {r['sender_filter']}")
            channel_note = f" → channel {r['target_channel_id']}" if r.get("target_channel_id") else ""
            lines.append(f"  [{r['rule_id']}] Match [{', '.join(parts)}] → {r.get('prompt', '')}{channel_note}")
        return "\n".join(lines)

    def list_discord_channels(
        self,
        tool_context: ToolContext = None,
    ) -> str:
        """
        List Discord channels the bot has previously been active in for this user.

        Use this to look up a channel ID when the user wants to route email
        summaries to a specific Discord channel by name.

        Returns:
            Formatted list of known channels with their IDs, names, and guild info.
        """
        phone, err = require_user_id(tool_context, "list discord channels")
        if err:
            return err

        db = self._get_firestore()
        if not db:
            return "ERROR: Database not available."

        user_id = phone
        try:
            channels_ref = (
                db.collection("discord_channels")
                .document(user_id)
                .collection("channels")
                .order_by("last_seen_at", direction="DESCENDING")
                .limit(20)
            )
            docs = list(channels_ref.stream())
        except Exception as e:
            logger.exception("[gmail-tool] list_discord_channels error")
            return f"Failed to list channels: {e}"

        if not docs:
            return (
                "No Discord channels recorded yet. "
                "The bot learns channels as you interact with it in Discord servers or DMs."
            )

        lines = [f"Known Discord channels ({len(docs)}):"]
        for doc in docs:
            c = doc.to_dict() or {}
            guild_note = f" (server {c['guild_id']})" if c.get("guild_id") else " (DM)"
            name_note = f" #{c['channel_name']}" if c.get("channel_name") else ""
            lines.append(f"  {c.get('channel_id', doc.id)}{name_note}{guild_note}")
        return "\n".join(lines)
