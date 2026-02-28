"""Email webhook handler for Gmail Pub/Sub push notifications.

Routes:
  POST /webhook/email          - Pub/Sub push endpoint
  GET  /internal/email/renew-watch  - Renew Gmail watch (called by Cloud Scheduler)
  GET  /internal/email/setup-watch  - One-time watch setup
"""
import base64
import json
import logging
from typing import Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from google.genai import types

from ..config import get_settings
from ..internal.auth import verify_internal_request
from .gmail_client import SYSTEM_FEATURE, SYSTEM_PHONE, GmailClient
from .authorizations import EmailAuthorizationsClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["email"])

# Module-level service references (set by init_email_services)
_gmail_client: Optional[GmailClient] = None
_authorizations: Optional[EmailAuthorizationsClient] = None
_agent_client = None
_session_manager = None

_DEFAULT_EMAIL_PROCESSING_PROMPT = (
    "Save the full email to Drive as a text file named YYYY-MM-DD_sender_subject.txt. "
    "Then extract: date, from, subject, a 1-sentence summary, action items (comma-separated), "
    "and category. Append a row to the sheet with those values."
)

_EMAIL_WRAPPER = """\
INCOMING_EMAIL_PROCESSING

Process this email silently (no SMS/Slack reply).
Follow these instructions:
{instructions}

---
From: {from_}
To: {to}
Date: {date}
Subject: {subject}

{body}
"""


def init_email_services(
    gmail_client: GmailClient,
    authorizations: EmailAuthorizationsClient,
    agent_client,
    session_manager,
) -> None:
    """Initialize module-level service references. Called by main.py."""
    global _gmail_client, _authorizations, _agent_client, _session_manager
    _gmail_client = gmail_client
    _authorizations = authorizations
    _agent_client = agent_client
    _session_manager = session_manager
    logger.info("Email handler services initialized")


# ──────────────────────────── webhook ────────────────────────────


@router.post("/webhook/email")
async def handle_email_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Receive Gmail Pub/Sub push notification.

    Pub/Sub sends a POST with a JSON body like:
    {
      "message": {
        "data": "<base64-encoded JSON with historyId>",
        "messageId": "...",
        ...
      },
      "subscription": "..."
    }
    """
    if not _gmail_client or not _authorizations or not _agent_client:
        logger.error("Email services not initialized")
        # Return 200 to avoid Pub/Sub retries while services are down
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        logger.warning("Email webhook: invalid JSON body")
        return Response(status_code=200)

    message = body.get("message", {})
    data_b64 = message.get("data", "")
    if not data_b64:
        return Response(status_code=200)

    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except Exception as e:
        logger.warning(f"Email webhook: could not decode message data: {e}")
        return Response(status_code=200)

    history_id = str(payload.get("historyId", ""))
    if not history_id:
        logger.debug("Email webhook: no historyId in payload")
        return Response(status_code=200)

    logger.info(f"Email webhook received, historyId={history_id}")
    background_tasks.add_task(process_email_notification, history_id)

    return Response(status_code=200)


# ──────────────────────────── background task ────────────────────────────


async def process_email_notification(history_id: str) -> None:
    """Fetch new messages and route each to the owning user's agent session."""
    try:
        emails = await _gmail_client.get_new_messages(history_id)
    except Exception as e:
        logger.error(f"Failed to fetch messages for historyId={history_id}: {e}")
        return

    if not emails:
        logger.debug(f"No new messages for historyId={history_id}")
        return

    for email in emails:
        sender = _extract_email_address(email.get("from", ""))
        if not sender:
            continue

        try:
            workflow = await _authorizations.get_authorization(sender)
        except Exception as e:
            logger.error(f"Firestore lookup failed for sender {sender}: {e}")
            continue

        if not workflow:
            logger.info(f"Discarding email from unauthorized sender: {sender}")
            continue

        phone = workflow.get("authorized_user_phone", "")
        if not phone:
            logger.warning(f"Workflow for {sender} has no authorized_user_phone, skipping")
            continue

        logger.info(f"Routing email from {sender} to user {phone[:4]}****")
        await _route_email_to_agent(workflow, email)


async def _route_email_to_agent(workflow: dict, email: dict) -> None:
    """Send the incoming email prompt to the user's agent session."""
    phone = workflow.get("authorized_user_phone", "")
    instructions = workflow.get("processing_prompt") or _DEFAULT_EMAIL_PROCESSING_PROMPT
    try:
        session_info = await _session_manager.get_or_create_session(phone)

        attachments = email.get("attachments", [])
        supported = [a for a in attachments if a.get("bytes")]
        unsupported = [a for a in attachments if not a.get("bytes")]

        unsupported_notes = "\n".join(
            f"[Attachment: {a['filename']} — type not directly supported, content not available]"
            for a in unsupported
        )
        body_text = email.get("body", "") or email.get("snippet", "")
        body_with_notes = body_text + ("\n\n" + unsupported_notes if unsupported_notes else "")

        prompt = _EMAIL_WRAPPER.format(
            instructions=instructions,
            from_=email.get("from", ""),
            to=email.get("to", ""),
            date=email.get("date", ""),
            subject=email.get("subject", ""),
            body=body_with_notes,
        )

        if supported:
            attachment_parts = [
                types.Part(inline_data=types.Blob(mime_type=a["mime_type"], data=a["bytes"]))
                for a in supported
            ]
            message: Union[str, types.Content] = types.Content(
                role="user", parts=[types.Part(text=prompt)] + attachment_parts
            )
        else:
            message = prompt

        await _agent_client.send_message(
            user_id=phone,
            session_id=session_info.agent_session_id,
            message=message,
        )
        logger.info(f"Email routed to agent session {session_info.agent_session_id}")
    except Exception as e:
        logger.error(f"Failed to route email to agent for {phone[:4]}****: {e}")


def _extract_email_address(raw: str) -> str:
    """Extract the bare email from strings like 'Name <email@example.com>'."""
    if "<" in raw and ">" in raw:
        return raw.split("<")[1].split(">")[0].strip().lower()
    return raw.strip().lower()


# ──────────────────────────── internal endpoints ────────────────────────────


@router.get("/internal/email/renew-watch")
async def renew_gmail_watch(
    topic: str = Query(..., description="Full Pub/Sub topic name"),
    caller: str = Depends(verify_internal_request),
) -> dict:
    """Renew Gmail push watch. Called every ~6 days by Cloud Scheduler."""
    if not _gmail_client:
        raise HTTPException(status_code=503, detail="Email services not initialized")

    history_id = await _gmail_client.renew_watch(topic)
    if not history_id:
        raise HTTPException(status_code=500, detail="Failed to renew Gmail watch")

    logger.info(f"Gmail watch renewed by {caller}, new historyId={history_id}")
    return {"status": "renewed", "historyId": history_id}


@router.get("/internal/email/setup-watch")
async def setup_gmail_watch(
    topic: str = Query(..., description="Full Pub/Sub topic name"),
    caller: str = Depends(verify_internal_request),
) -> dict:
    """One-time Gmail watch setup."""
    if not _gmail_client:
        raise HTTPException(status_code=503, detail="Email services not initialized")

    history_id = await _gmail_client.setup_watch(topic)
    if not history_id:
        raise HTTPException(status_code=500, detail="Failed to set up Gmail watch")

    logger.info(f"Gmail watch set up by {caller}, historyId={history_id}")
    return {"status": "configured", "historyId": history_id}
