"""Email webhook handler for Gmail Pub/Sub push notifications.

Routes:
  POST /webhook/email                       - Pub/Sub push endpoint (all Gmail accounts)
  GET  /internal/email/renew-watch          - Renew Gmail watch (called by Cloud Scheduler)

All personal Gmail accounts share the same Pub/Sub topic.
Each notification carries an emailAddress that maps to an email_state/{doc_id}
Firestore document containing user_id, token_feature, and preferred_channel.
"""
import asyncio
import base64
import functools
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from ..internal.auth import verify_internal_request
from .gmail_client import (
    EMAIL_RULES_COLLECTION, get_gmail_token, get_new_messages, setup_watch,
    normalize_gmail_address,
)
from ..slack.sender import SlackSender

logger = logging.getLogger(__name__)

router = APIRouter(tags=["email"])

# Module-level service references (set by init_email_services)
_oauth_manager = None
_db = None  # AsyncFirestore client
_agent_client = None
_session_manager = None
_slack_sender: Optional[SlackSender] = None
_telegram_sender = None
_discord_sender = None

_NOTIFICATION_WRAPPER = """\
INCOMING_EMAIL_NOTIFICATION

OFFLINE MODE: You are operating autonomously without the user present (this
may be an email notification, a background task, or another automated trigger).
Any tool that requires interactive user confirmation will be rejected — you
will receive "This tool call is rejected." When that happens:
- If the operation can be completed without that tool, do so.
- Otherwise, send the user a concise message describing what you would have
  done and ask them to follow up on an interactive channel (Telegram or
  Discord) where they can approve it in real time.

If no user-visible reminder or summary is needed, start your response with:
<SUPPRESS RESPONSE>

A new email has arrived. It may contain attachments with important information
that you must read before processing.

IMPORTANT: Call fetch_email(message_id="{message_id}") first. This fetches
the full email body and stores any attachments (PDFs, images, docs) as
artifacts so you can read their content.

From: {from_}
Subject: {subject}
Message ID: {message_id}

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
    oauth_manager,
    db,
    agent_client,
    session_manager,
    slack_sender: Optional[SlackSender] = None,
    telegram_sender=None,
    discord_sender=None,
) -> None:
    """Initialize module-level service references. Called by main.py."""
    global _oauth_manager, _db, _agent_client
    global _session_manager, _slack_sender, _telegram_sender, _discord_sender
    _oauth_manager = oauth_manager
    _db = db
    _agent_client = agent_client
    _session_manager = session_manager
    _slack_sender = slack_sender
    _telegram_sender = telegram_sender
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
                audience=audience if audience else None,
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

    if not _oauth_manager or not _db:
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
    """Fetch new messages for any Gmail account and route each to the appropriate agent.

    The watch state doc drives which user_id, token, agent, and channel to use.
    """
    watch_state = await _read_watch_state(gmail_address)
    if not watch_state:
        logger.warning(f"No watch state found for {gmail_address} — ignoring notification")
        return

    user_id = watch_state.get("user_id", "")
    token_feature = watch_state.get("token_feature", "")
    preferred_channel = watch_state.get("preferred_channel", "discord")

    if not user_id or not token_feature:
        logger.warning(f"Watch state for {gmail_address} is missing user_id or token_feature")
        return

    token = await get_gmail_token(_oauth_manager, user_id, token_feature)
    if not token:
        logger.error(f"No {token_feature} token for {user_id[:4]}**** — cannot fetch messages")
        return

    start_history_id = watch_state.get("last_history_id")
    if not start_history_id:
        start_history_id = str(int(history_id) - 1)
        logger.warning(
            f"No stored history_id for {gmail_address}; using fallback start={start_history_id}"
        )

    try:
        emails = await get_new_messages(token, start_history_id, history_id)
    except Exception as e:
        logger.error(f"Failed to fetch messages for {gmail_address}: {e}")
        return

    await _write_watch_state(gmail_address, {"last_history_id": history_id})

    if not emails:
        logger.debug(f"No new messages for {gmail_address}")
        return

    rules = await _load_email_rules(user_id)
    rules_text = _format_rules_for_prompt(rules)

    for email in emails:
        logger.info(
            f"Routing email from {email.get('from', '')[:30]}... "
            f"(user={user_id[:4]}****, channel={preferred_channel})"
        )
        await _route_email_to_agent(email, user_id, preferred_channel, rules_text)


async def _route_email_to_agent(
    email: dict,
    user_id: str,
    channel: str,
    rules_text: str,
) -> None:
    """Send an email notification prompt to the agent and reply via channel."""
    if not _agent_client:
        logger.error("Agent client not initialized")
        return

    try:
        session_info = await _session_manager.get_or_create_session(
            user_id, channel="email"
        )

        prompt = _NOTIFICATION_WRAPPER.format(
            from_=email.get("from", ""),
            subject=email.get("subject", ""),
            message_id=email.get("id", ""),
            rules=rules_text,
        )

        events = await _agent_client.send_message_events(
            user_id=user_id,
            session_id=session_info.agent_session_id,
            message=prompt,
        )
        logger.info(f"Email routed to agent session {session_info.agent_session_id}")

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
            await _send_response(user_id, channel, response)
    except Exception as e:
        logger.error(f"Failed to route email for {user_id[:4]}****: {e}")


async def _send_response(user_id: str, channel: str, message: str) -> None:
    """Send an agent response to the user via their preferred channel."""
    try:
        if channel == "slack" and _slack_sender:
            await _slack_sender.send(user_id, message)
        elif channel == "telegram" and _telegram_sender:
            await _telegram_sender.send(user_id, message)
        elif channel == "discord" and _discord_sender:
            await _discord_sender.send(user_id, message)
        else:
            logger.warning(f"No sender available for channel {channel!r}")
    except Exception as e:
        logger.error(f"Failed to send email response via {channel}: {e}")


def _extract_email_address(raw: str) -> str:
    """Extract the bare email from strings like 'Name <email@example.com>'."""
    if "<" in raw and ">" in raw:
        return raw.split("<")[1].split(">")[0].strip().lower()
    return raw.strip().lower()


async def register_gmail_watch(
    user_id: str,
    gmail_address: str,
    feature: str,
    preferred_channel: str = "discord",
) -> bool:
    """Set up Gmail push watch for a user after OAuth authorization.

    Called automatically from the OAuth callback when a user authorizes
    the `google` feature, which includes Gmail scope.

    Args:
        user_id: User identifier or Discord ID.
        gmail_address: The authorized Gmail address.
        feature: OAuth feature, currently `google`.
        preferred_channel: Channel to deliver email notifications on.

    Returns:
        True if watch was set up successfully, False otherwise.
    """
    if not _oauth_manager or not _db:
        logger.warning("Email services not initialized — skipping Gmail watch setup")
        return False

    from ..config import get_settings
    settings = get_settings()
    topic = settings.EMAIL_PUBSUB_TOPIC
    if not topic:
        logger.warning("EMAIL_PUBSUB_TOPIC not configured — skipping Gmail watch setup")
        return False

    token = await get_gmail_token(_oauth_manager, user_id, feature)
    if not token:
        logger.error(f"No {feature} token for {user_id[:4]}**** — cannot set up Gmail watch")
        return False

    watch_result = await setup_watch(token, topic)
    if not watch_result:
        logger.error(f"Failed to set up Gmail watch for {gmail_address}")
        return False

    from datetime import datetime, timezone
    history_id = str(watch_result.get("historyId", ""))
    expiration = watch_result.get("expiration", "")

    await _write_watch_state(
        gmail_address,
        {
            "gmail_address": gmail_address,
            "user_id": user_id,
            "token_feature": feature,
            "preferred_channel": preferred_channel,
            "last_history_id": history_id,
            "watch_expiration": expiration,
            "updated_at": datetime.now(timezone.utc),
        },
    )

    logger.info(
        f"Gmail watch registered for {gmail_address} (user={user_id[:4]}****, "
        f"feature={feature}, historyId={history_id})"
    )
    return True


# ──────────────────────────── internal endpoints ────────────────────────────


@router.get("/internal/email/renew-watch")
async def renew_gmail_watch(
    topic: str = Query(..., description="Full Pub/Sub topic name"),
    caller: str = Depends(verify_internal_request),
) -> dict:
    """Renew Gmail push watches for all registered accounts.

    Called every ~6 days by Cloud Scheduler. Iterates all email_state/* documents
    and renews each watch using the stored user_id and token_feature.
    """
    if not _oauth_manager or not _db:
        raise HTTPException(status_code=503, detail="Email services not initialized")

    renewed = 0
    failed = 0

    try:
        async for doc in _db.collection("email_state").stream():
            data = doc.to_dict() or {}
            gmail_address = data.get("gmail_address", "")
            user_id = data.get("user_id", "")
            token_feature = data.get("token_feature", "")

            if not gmail_address or not user_id or not token_feature:
                logger.warning(f"Skipping email_state/{doc.id}: missing required fields")
                continue

            token = await get_gmail_token(_oauth_manager, user_id, token_feature)
            if not token:
                logger.warning(
                    f"No {token_feature} token for {user_id[:4]}**** "
                    f"— skipping watch renewal for {gmail_address}"
                )
                failed += 1
                continue

            watch_result = await setup_watch(token, topic)
            if watch_result:
                new_hid = str(watch_result.get("historyId", ""))
                expiration = watch_result.get("expiration", "")
                await _write_watch_state(
                    gmail_address,
                    {"last_history_id": new_hid, "watch_expiration": expiration},
                )
                logger.info(f"Watch renewed for {gmail_address}")
                renewed += 1
            else:
                logger.warning(f"Failed to renew watch for {gmail_address}")
                failed += 1
    except Exception as e:
        logger.error(f"Error iterating email_state documents: {e}")

    logger.info(f"Watch renewal complete by {caller}: {renewed} renewed, {failed} failed")
    return {
        "status": "renewed",
        "renewed": renewed,
        "failed": failed,
    }
