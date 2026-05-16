"""Email webhook handler for Gmail Pub/Sub push notifications.

Routes:
  POST /webhook/email                       - Pub/Sub push endpoint (all Gmail accounts)
  GET  /internal/email/renew-watch          - Renew Gmail watch (called by Cloud Scheduler)

All personal Gmail accounts share the same Pub/Sub topic.
Each notification carries an emailAddress that maps to an email_state/{doc_id}
Firestore document containing user_id and token_feature.
"""
import asyncio
import base64
import functools
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from google.cloud import firestore

from ..internal.auth import verify_internal_request
from .gmail_client import EMAIL_RULES_COLLECTION, normalize_gmail_address

logger = logging.getLogger(__name__)

router = APIRouter(tags=["email"])

# Module-level service references (set by init_email_services)
_db = None  # AsyncFirestore client
_agent_client = None
_session_manager = None
_discord_sender = None

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

IMPORTANT: Call read_emails(since_history_id="{start_history_id}") to get the
new messages. For each message ID returned, call fetch_email(message_id=...) to
read the full content and any attachments before processing.

Your active email rules:
{rules}

If a rule matches, follow its instructions exactly. If no rule matches, use smart defaults:
- Calendar invites / flight or hotel confirmations / appointments → create_calendar_event + notify
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
) -> None:
    """Initialize module-level service references. Called by main.py."""
    global _db, _agent_client
    global _session_manager, _discord_sender
    _db = db
    _agent_client = agent_client
    _session_manager = session_manager
    _discord_sender = discord_sender
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

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        loop = asyncio.get_running_loop()
        claims = await loop.run_in_executor(
            None,
            functools.partial(
                id_token.verify_oauth2_token,
                token,
                google_requests.Request(),
                audience=audience,
            ),
        )
    except Exception as e:
        logger.warning(f"Email webhook: OIDC token verification failed: {e}")
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
        logger.error(f"Failed to read watch state for {gmail_address}: {e}")
        return None


async def _write_watch_state(gmail_address: str, updates: dict) -> None:
    """Update watch state fields for a Gmail address (merge=True)."""
    if not _db:
        return
    try:
        doc_id = normalize_gmail_address(gmail_address)
        await _db.collection("email_state").document(doc_id).set(updates, merge=True)
    except Exception as e:
        logger.error(f"Failed to write watch state for {gmail_address}: {e}")


_ALREADY_PROCESSED = object()


async def _atomic_advance_history_id(
    gmail_address: str,
    new_history_id: str,
) -> object:
    """Atomically advance last_history_id and return the prior value.

    Returns _ALREADY_PROCESSED if new_history_id is not greater than the
    stored value (concurrent notification already covered this range).
    Returns None if there was no stored history_id (caller should use fallback).
    Returns the stored history_id string otherwise.
    """
    if not _db:
        return None
    doc_id = normalize_gmail_address(gmail_address)
    doc_ref = _db.collection("email_state").document(doc_id)

    @firestore.async_transactional
    async def _txn(transaction):
        doc = await doc_ref.get(transaction=transaction)
        data = doc.to_dict() if doc.exists else {}
        stored_id = data.get("last_history_id")
        if stored_id and int(stored_id) >= int(new_history_id):
            return _ALREADY_PROCESSED
        transaction.set(doc_ref, {"last_history_id": new_history_id}, merge=True)
        return stored_id

    try:
        transaction = _db.transaction()
        return await _txn(transaction)
    except Exception as e:
        logger.error(f"Failed to advance history_id for {gmail_address}: {e}")
        return None


# ──────────────────────────── email rules ────────────────────────────


async def _load_email_rules(user_id: str) -> list[dict]:
    """Load email rules for a user from email_rules/{user_id}/rules/."""
    if not _db:
        return []
    try:
        rules_ref = _db.collection(EMAIL_RULES_COLLECTION).document(user_id).collection("rules")
        return [doc.to_dict() async for doc in rules_ref.stream()]
    except Exception as e:
        logger.error(f"Failed to load email rules for {user_id[:4]}****: {e}")
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
        lines.append(f"- Match [{', '.join(parts)}] → {r.get('prompt', '')}")
    return "\n".join(lines)


# ──────────────────────────── unified notification processing ────────────────────────────


async def process_email_notification(gmail_address: str, history_id: str) -> None:
    """Route a Gmail push notification to the agent.

    The agent fetches the actual emails using its own IAM connector credentials.
    """
    watch_state = await _read_watch_state(gmail_address)
    if not watch_state:
        logger.warning(f"No watch state found for {gmail_address} — ignoring notification")
        return

    user_id = watch_state.get("user_id", "")
    if not user_id:
        logger.warning(f"Watch state for {gmail_address} is missing user_id")
        return

    # Atomically advance last_history_id so concurrent Pub/Sub notifications
    # cannot process the same email range twice.
    prior_history_id = await _atomic_advance_history_id(gmail_address, history_id)
    if prior_history_id is _ALREADY_PROCESSED:
        logger.debug(f"Notification history_id={history_id} for {gmail_address} already processed")
        return

    start_history_id = prior_history_id
    if not start_history_id:
        start_history_id = str(int(history_id) - 1)
        logger.warning(
            f"No stored history_id for {gmail_address}; using fallback start={start_history_id}"
        )

    rules = await _load_email_rules(user_id)
    rules_text = _format_rules_for_prompt(rules)

    logger.info(f"Routing email notification for {gmail_address} (user={user_id[:4]}****)")
    await _notify_agent_of_emails(gmail_address, start_history_id, user_id, rules_text)


async def _notify_agent_of_emails(
    gmail_address: str,
    start_history_id: str,
    user_id: str,
    rules_text: str,
) -> None:
    """Send an email push notification to the agent."""
    if not _agent_client:
        logger.error("Agent client not initialized")
        return

    try:
        session_info = await _session_manager.get_or_create_session(
            user_id, channel="email"
        )

        prompt = _NOTIFICATION_WRAPPER.format(
            gmail_address=gmail_address,
            start_history_id=start_history_id,
            rules=rules_text,
        )

        events = await _agent_client.send_message_events(
            user_id=user_id,
            session_id=session_info.agent_session_id,
            message=prompt,
        )
        logger.info(f"Email notification routed to agent session {session_info.agent_session_id}")

        # Handle IAM connector credential requests (offline: store pending + send auth link)
        credential_requests = _agent_client.extract_credential_requests(events)
        if credential_requests:
            req = credential_requests[0]
            logger.info(
                f"Offline IAM connector credential request for {user_id[:4]}****: "
                f"nonce={req.nonce[:8]}..."
            )
            await _session_manager.set_pending_credential(
                nonce=req.nonce,
                user_id=user_id,
                session_id=session_info.agent_session_id,
                credential_function_call_id=req.function_call_id,
                auth_config_dict=req.auth_config_dict,
                auth_uri=req.auth_uri,
            )
            await _send_response(
                user_id,
                f"An email triggered an action that requires Google authorization. "
                f"Click here to authorize and I'll complete it automatically:\n{req.auth_uri}",
            )
            return

        confirmations = _agent_client.extract_confirmation_requests(events)
        if confirmations:
            tool_names = [confirmation.tool_name for confirmation in confirmations]
            logger.warning(
                f"Offline ADK confirmations for {user_id[:4]}****: {tool_names} — declining and collecting fallback response"
            )
            follow_up_events: list = []
            for confirmation in confirmations:
                follow_up_events = await _agent_client.send_confirmation_response(
                    user_id=user_id,
                    session_id=session_info.agent_session_id,
                    confirmation_function_call_id=confirmation.function_call_id,
                    confirmed=False,
                )
            response = _agent_client.extract_text(follow_up_events)
        else:
            response = _agent_client.extract_text(events)
        if response:
            if _should_suppress_response(response):
                logger.info(f"Email response suppressed for {user_id[:4]}****")
                return
            await _send_response(user_id, response)
    except Exception as e:
        logger.error(f"Failed to route email notification for {user_id[:4]}****: {e}")


async def _send_response(user_id: str, message: str) -> None:
    """Send an agent response to the user via Discord."""
    try:
        if _discord_sender:
            await _discord_sender.send(user_id, message)
        else:
            logger.warning("No Discord sender available for email response")
    except Exception as e:
        logger.error(f"Failed to send email response via Discord: {e}")



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
        logger.info(f"Gmail watch setup requested for user={user_id[:4]}****")
        return True
    except Exception as e:
        logger.error(f"Failed to request Gmail watch setup for {user_id[:4]}****: {e}")
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
        logger.error(f"Error iterating email_state documents: {e}")

    logger.info(f"Watch renewal complete by {caller}: {renewed} requested, {failed} failed")
    return {
        "status": "renewed",
        "renewed": renewed,
        "failed": failed,
    }
