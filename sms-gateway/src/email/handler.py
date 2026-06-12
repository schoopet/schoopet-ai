"""Email webhook handler for Gmail Pub/Sub push notifications.

Routes:
  POST /webhook/email                       - Pub/Sub push endpoint (all Gmail accounts)
  GET  /internal/email/renew-watch          - Renew Gmail watch (called by Cloud Scheduler)

All personal Gmail accounts share the same Pub/Sub topic.
Each notification carries an emailAddress that maps to an email_state/{doc_id}
Firestore document containing user_id and token_feature.
"""
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from google.cloud import firestore

from ..internal.auth import verify_internal_request, _verify_oidc_claims_for_audience
from .gmail_client import EMAIL_RULES_COLLECTION, normalize_gmail_address

logger = logging.getLogger(__name__)

router = APIRouter(tags=["email"])

# Module-level service references (set by init_email_services)
_db = None  # AsyncFirestore client
_agent_client = None
_session_manager = None
_discord_sender = None
_task_executor = None

_NOTIFICATION_WRAPPER = """\
INCOMING_EMAIL_NOTIFICATION

OFFLINE MODE: You are operating autonomously without the user present (this
may be an email notification, a background task, or another automated trigger).
Any tool that requires interactive user confirmation will be rejected — you
will receive "This tool call is rejected." When that happens:
- If the operation can be completed without that tool, do so.
- Otherwise, send the user a concise message describing what you would have
  done and ask them to follow up on Discord where they can approve it in real time.

If no user-visible reminder or summary is needed, start your response with:
<SUPPRESS RESPONSE>

One or more new emails have arrived for {gmail_address}.

IMPORTANT: Call atomic_read_received_emails() to get the new messages.
If it returns "No new emails.", respond with <SUPPRESS RESPONSE> and stop.
For each message ID returned, call fetch_email(message_id=...) to read the full
content and any attachments before processing.

Your active email rules:
{rules}

CHANNEL ROUTING: Some rules include a "Route to channel" directive. When a rule
with a channel directive matches, wrap your summary for that email in:
  <CHANNEL:channel_id>Your summary here.</CHANNEL>
Do NOT include that content anywhere else in your response. Each email that
matches a routed rule must have its own <CHANNEL:...>...</CHANNEL> block.
Untagged content (or content not inside a CHANNEL block) is sent to the user's
default Discord DM. Rules without a channel directive use that default path.

If a rule matches, follow its instructions exactly. If no rule matches, use smart defaults:
- Calendar invites / flight or hotel confirmations / appointments → summarise the email and ask the user if they want an event created; do NOT call create_calendar_event
- Invoices / deadlines / deliveries / action required → create_async_task + notify
- Newsletters / promotions / automated digests → respond with <SUPPRESS RESPONSE>"""


def _should_suppress_response(message: str) -> bool:
    """Return True when the agent explicitly marks an email response as suppressed."""
    return message.lstrip().startswith("<SUPPRESS RESPONSE>")


def init_email_services(
    db,
    agent_client,
    session_manager,
    discord_sender=None,
    task_executor=None,
) -> None:
    """Initialize module-level service references. Called by main.py."""
    global _db, _agent_client
    global _session_manager, _discord_sender, _task_executor
    _db = db
    _agent_client = agent_client
    _session_manager = session_manager
    _discord_sender = discord_sender
    _task_executor = task_executor
    logger.info("Email handler services initialized")


# ──────────────────────────── webhook ────────────────────────────


async def _verify_pubsub_oidc(request: Request) -> None:
    """Verify the OIDC token Pub/Sub attaches when EMAIL_PUSH_SERVICE_ACCOUNT is set."""
    expected_sa = os.getenv("EMAIL_PUSH_SERVICE_ACCOUNT", "")
    if not expected_sa:
        logger.warning("EMAIL_PUSH_SERVICE_ACCOUNT not set — skipping Pub/Sub OIDC verification")
        return

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        logger.warning("Email webhook: missing or malformed Authorization header")
        raise HTTPException(status_code=401, detail="Missing Pub/Sub OIDC token")

    token = authorization[7:]
    audience = os.getenv("SMS_GATEWAY_URL", "")
    if not audience:
        logger.error("SMS_GATEWAY_URL not set — cannot validate Pub/Sub OIDC audience")
        raise HTTPException(status_code=503, detail="Server misconfigured: missing audience")

    claims = await _verify_oidc_claims_for_audience(token, audience)
    if claims is None:
        logger.warning("Email webhook: OIDC token verification failed")
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub OIDC token")

    email = claims.get("email", "")
    if email != expected_sa:
        logger.warning(f"Email webhook: unexpected token SA {email!r}, want {expected_sa!r}")
        raise HTTPException(status_code=403, detail="Unauthorized Pub/Sub sender")


@router.post("/webhook/email")
async def handle_email_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Receive Gmail Pub/Sub push notification.

    Pub/Sub sends a POST with a JSON body:
    {
      "message": {
        "data": "<base64-encoded JSON with emailAddress and historyId>",
        ...
      }
    }

    emailAddress identifies which personal Gmail account triggered the notification.
    """
    await _verify_pubsub_oidc(request)

    if not _db:
        logger.error("Email services not initialized")
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        logger.warning("Email webhook: invalid JSON body", exc_info=True)
        return Response(status_code=200)

    message = body.get("message", {})
    data_b64 = message.get("data", "")
    if not data_b64:
        return Response(status_code=200)

    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except Exception as e:
        logger.warning(f"Email webhook: could not decode message data: {e}", exc_info=True)
        return Response(status_code=200)

    history_id = str(payload.get("historyId", ""))
    email_address = payload.get("emailAddress", "").lower().strip()

    if not history_id:
        logger.debug("Email webhook: no historyId in payload")
        return Response(status_code=200)

    if not email_address:
        logger.warning("Email webhook: no emailAddress in payload — cannot route notification")
        return Response(status_code=200)

    logger.info(f"Email webhook: historyId={history_id}, emailAddress={email_address}")
    background_tasks.add_task(process_email_notification, email_address, history_id)
    return Response(status_code=200)


# ──────────────────────────── unified watch state ────────────────────────────


async def _read_watch_state(gmail_address: str) -> Optional[dict]:
    """Read watch state from email_state/{normalized_address} in Firestore."""
    if not _db:
        return None
    try:
        doc_id = normalize_gmail_address(gmail_address)
        doc = await _db.collection("email_state").document(doc_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        logger.exception(f"Failed to read watch state for {gmail_address}")
        return None


async def _write_watch_state(gmail_address: str, updates: dict) -> None:
    """Update watch state fields for a Gmail address (merge=True)."""
    if not _db:
        return
    try:
        doc_id = normalize_gmail_address(gmail_address)
        await _db.collection("email_state").document(doc_id).set(updates, merge=True)
    except Exception as e:
        logger.exception(f"Failed to write watch state for {gmail_address}")


async def _atomic_update_last_received(gmail_address: str, history_id: str) -> bool:
    """Atomically advance last_received_id and claim the right to schedule a task.

    Returns True if a new async task should be created (no task was pending).
    Returns False if the notification is a duplicate or a task is already scheduled.
    Sets pending_task_id to "SCHEDULING" as a sentinel when returning True, so
    concurrent webhook deliveries cannot both schedule a task.
    """
    if not _db:
        return False
    doc_id = normalize_gmail_address(gmail_address)
    doc_ref = _db.collection("email_state").document(doc_id)

    @firestore.async_transactional
    async def _txn(transaction):
        doc = await doc_ref.get(transaction=transaction)
        data = doc.to_dict() if doc.exists else {}
        stored_received = data.get("last_received_id") or data.get("last_history_id")
        if stored_received and int(stored_received) >= int(history_id):
            return False
        updates = {"last_received_id": history_id}
        if data.get("pending_task_id"):
            transaction.set(doc_ref, updates, merge=True)
            return False
        updates["pending_task_id"] = "SCHEDULING"
        transaction.set(doc_ref, updates, merge=True)
        return True

    try:
        transaction = _db.transaction()
        return await _txn(transaction)
    except Exception:
        logger.exception(f"Failed to update last_received_id for {gmail_address}")
        return False


# ──────────────────────────── email rules ────────────────────────────


async def _load_email_rules(user_id: str) -> list[dict]:
    """Load email rules for a user from email_rules/{user_id}/rules/."""
    if not _db:
        return []
    try:
        rules_ref = _db.collection(EMAIL_RULES_COLLECTION).document(user_id).collection("rules")
        return [doc.to_dict() async for doc in rules_ref.stream()]
    except Exception as e:
        logger.exception(f"Failed to load email rules for {user_id}")
        return []


def _format_rules_for_prompt(rules: list[dict]) -> str:
    """Format email rules for inclusion in the agent notification prompt."""
    if not rules:
        return "(no rules configured — use smart defaults)"
    lines = []
    for r in rules:
        parts = []
        if r.get("topic"):
            parts.append(f"topic: {r['topic']}")
        if r.get("sender_filter"):
            parts.append(f"sender: {r['sender_filter']}")
        line = f"- Match [{', '.join(parts)}] → {r.get('prompt', '')}"
        if r.get("target_channel_id"):
            line += f"\n  Route to channel: wrap your summary in <CHANNEL:{r['target_channel_id']}>...</CHANNEL>"
        lines.append(line)
    return "\n".join(lines)


# ──────────────────────────── unified notification processing ────────────────────────────


async def process_email_notification(gmail_address: str, history_id: str) -> None:
    """Schedule an async task to process new emails for a Gmail address.

    Debounces rapid Pub/Sub bursts: only one task is scheduled per user at a time.
    Subsequent notifications while a task is pending just advance last_received_id
    so the task picks up the full batch when it fires 5 minutes later.
    """
    watch_state = await _read_watch_state(gmail_address)
    if not watch_state:
        logger.warning(f"No watch state found for {gmail_address} — ignoring notification")
        return

    user_id = watch_state.get("user_id", "")
    if not user_id:
        logger.warning(f"Watch state for {gmail_address} is missing user_id")
        return

    needs_task = await _atomic_update_last_received(gmail_address, history_id)
    if not needs_task:
        logger.debug(
            f"Email notification history_id={history_id} for {gmail_address}: "
            "deduped or task already pending"
        )
        return

    rules = await _load_email_rules(user_id)
    prompt = _NOTIFICATION_WRAPPER.format(
        gmail_address=gmail_address,
        rules=_format_rules_for_prompt(rules),
    )
    debounce_minutes = int(os.getenv("EMAIL_DEBOUNCE_MINUTES", "15"))
    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=debounce_minutes)
    discord_channel_id = watch_state.get("discord_channel_id", "")

    doc_id = normalize_gmail_address(gmail_address)
    doc_ref = _db.collection("email_state").document(doc_id)
    try:
        task_id = await _task_executor.create_email_batch_task(
            gmail_address=gmail_address,
            user_id=user_id,
            prompt=prompt,
            discord_channel_id=discord_channel_id,
            scheduled_at=scheduled_at,
        )
        await doc_ref.set({"pending_task_id": task_id}, merge=True)
        first_id = watch_state.get("last_processed_id") or watch_state.get("last_history_id")
        logger.info(
            "EMAIL_TASK_SCHEDULED time=%s user=%s first_id=%s last_id=%s task_id=%s",
            scheduled_at, user_id, first_id, history_id, task_id,
        )
    except Exception:
        logger.exception(f"Failed to create email batch task for {gmail_address}")
        await doc_ref.set({"pending_task_id": ""}, merge=True)




async def register_gmail_watch(
    user_id: str,
    gmail_address: str = "",
) -> bool:
    """Ask the agent to register a Gmail push watch.

    Called after OAuth consent completes. The agent uses its own IAM connector
    credentials to call the Gmail watch API and writes the result to Firestore.
    """
    if not _agent_client or not _session_manager:
        logger.warning("Email services not initialized — skipping Gmail watch setup")
        return False

    try:
        session_info = await _session_manager.get_or_create_session(
            user_id, channel="email"
        )
        await _agent_client.send_message_events(
            user_id=user_id,
            session_id=session_info.agent_session_id,
            message="__ADMIN:setup_gmail_watch__",
        )
        logger.info(f"Gmail watch setup requested for user={user_id}")
        return True
    except Exception as e:
        logger.exception(f"Failed to request Gmail watch setup for {user_id}")
        return False


# ──────────────────────────── internal endpoints ────────────────────────────


@router.get("/internal/email/renew-watch")
async def renew_gmail_watch(
    topic: str = Query(default="", description="Ignored — agent reads topic from env"),
    caller: str = Depends(verify_internal_request),
) -> dict:
    """Renew Gmail push watches for all registered accounts.

    Called every ~6 days by Cloud Scheduler. Asks each user's agent to renew
    the Gmail watch using its own IAM connector credentials.
    """
    if not _db:
        raise HTTPException(status_code=503, detail="Email services not initialized")

    renewed = 0
    failed = 0

    try:
        async for doc in _db.collection("email_state").stream():
            data = doc.to_dict() or {}
            user_id = data.get("user_id", "")
            gmail_address = data.get("gmail_address", "")

            if not user_id:
                logger.warning(f"Skipping email_state/{doc.id}: missing user_id")
                failed += 1
                continue

            success = await register_gmail_watch(user_id, gmail_address)
            if success:
                logger.info(f"Watch renewal requested for {gmail_address}")
                renewed += 1
            else:
                failed += 1
    except Exception as e:
        logger.exception(f"Error iterating email_state documents")

    logger.info(f"Watch renewal complete by {caller}: {renewed} requested, {failed} failed")
    return {
        "status": "renewed",
        "renewed": renewed,
        "failed": failed,
    }
