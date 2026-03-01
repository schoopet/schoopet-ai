"""Email webhook handler for Gmail Pub/Sub push notifications.

Routes:
  POST /webhook/email          - Pub/Sub push endpoint
  GET  /internal/email/renew-watch  - Renew Gmail watch (called by Cloud Scheduler)
  GET  /internal/email/setup-watch  - One-time watch setup
"""
import asyncio
import base64
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from ..internal.auth import verify_internal_request
from .gmail_client import SYSTEM_FEATURE, SYSTEM_PHONE, GmailClient
from .authorizations import EmailAuthorizationsClient
from ..slack.sender import SlackSender

logger = logging.getLogger(__name__)

router = APIRouter(tags=["email"])

# Module-level service references (set by init_email_services)
_gmail_client: Optional[GmailClient] = None
_authorizations: Optional[EmailAuthorizationsClient] = None
_agent_client = None
_session_manager = None
_slack_sender: Optional[SlackSender] = None

_DEFAULT_EMAIL_PROCESSING_PROMPT = (
    "Save the full email to Drive as a text file named YYYY-MM-DD_sender_subject.txt. "
    "Then extract: date, from, subject, a 1-sentence summary, action items (comma-separated), "
    "and category. Append a row to the sheet with those values."
)

_NOTIFICATION_WRAPPER = """\
INCOMING_EMAIL_NOTIFICATION

A new email has arrived from an authorized sender. It may contain attachments
with important information that you must read before processing.

IMPORTANT: Call fetch_email(message_id="{message_id}") first. This fetches
the full email body and stores any attachments (PDFs, images, docs) as
artifacts so you can read their content and save them to Drive.

From: {from_}
Subject: {subject}
Message ID: {message_id}

Processing instructions:
{instructions}"""


def init_email_services(
    gmail_client: GmailClient,
    authorizations: EmailAuthorizationsClient,
    agent_client,
    session_manager,
    slack_sender: Optional[SlackSender] = None,
) -> None:
    """Initialize module-level service references. Called by main.py."""
    global _gmail_client, _authorizations, _agent_client, _session_manager, _slack_sender
    _gmail_client = gmail_client
    _authorizations = authorizations
    _agent_client = agent_client
    _session_manager = session_manager
    _slack_sender = slack_sender
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


async def _read_last_history_id() -> Optional[str]:
    """Read last_history_id from email_state/watch in Firestore."""
    if not _authorizations:
        return None
    try:
        doc = await _authorizations._db.collection("email_state").document("watch").get()
        if doc.exists:
            val = doc.to_dict().get("last_history_id")
            return str(val) if val else None
        return None
    except Exception as e:
        logger.error(f"Failed to read last_history_id from Firestore: {e}")
        return None


async def _write_last_history_id(history_id: str) -> None:
    """Persist last_history_id to email_state/watch in Firestore."""
    if not _authorizations:
        return
    try:
        await _authorizations._db.collection("email_state").document("watch").set(
            {"last_history_id": history_id}, merge=True
        )
    except Exception as e:
        logger.error(f"Failed to write last_history_id to Firestore: {e}")


async def process_email_notification(history_id: str) -> None:
    """Fetch new messages and route each to the owning user's agent session."""
    # Read the previously stored historyId as the query baseline.
    # Gmail's history.list returns records *strictly after* startHistoryId,
    # so we must pass the last value we saw — not the current notification's id.
    start_history_id = await _read_last_history_id()
    if start_history_id is None:
        # First run: fall back to one before the notification's id.
        start_history_id = str(int(history_id) - 1)
        logger.warning(
            f"No stored last_history_id; using fallback start={start_history_id} "
            f"(notification historyId={history_id})"
        )

    try:
        emails = await _gmail_client.get_new_messages(start_history_id, history_id)
    except Exception as e:
        logger.error(f"Failed to fetch messages for historyId={history_id}: {e}")
        return

    # Advance the stored baseline to the current notification's historyId
    # so the next notification queries from here onwards.
    await _write_last_history_id(history_id)

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
    """Send an email notification prompt to the user's agent session."""
    phone = workflow.get("authorized_user_phone", "")
    instructions = workflow.get("processing_prompt") or _DEFAULT_EMAIL_PROCESSING_PROMPT
    try:
        session_info = await _session_manager.get_or_create_session(phone)

        prompt = _NOTIFICATION_WRAPPER.format(
            from_=email.get("from", ""),
            subject=email.get("subject", ""),
            message_id=email.get("id", ""),
            instructions=instructions,
        )

        response = await _agent_client.send_message(
            user_id=phone,
            session_id=session_info.agent_session_id,
            message=prompt,
        )
        logger.info(f"Email routed to agent session {session_info.agent_session_id}")

        if response and _slack_sender:
            await _slack_sender.send(phone, response)
            logger.info(f"Email response sent to Slack user {phone[:4]}****")
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

    await _write_last_history_id(history_id)
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

    await _write_last_history_id(history_id)
    logger.info(f"Gmail watch set up by {caller}, historyId={history_id}")
    return {"status": "configured", "historyId": history_id}
