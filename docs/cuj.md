# Critical User Journeys

## 1. Discord DM / @mention → agent response (happy path)

User messages the bot → `SchoopetGateway` (persistent WebSocket connection) receives it → rate-limit check → `SessionManager.get_or_create_session()` (keyed by Discord user ID + channel scope, stored in Firestore `sms_sessions`) → `AgentEngineClient.send_message_events()` → response dispatched to Discord.

Session is reused within 30 min; after that a new Agent Engine session is created.

**Key files:** `sms-gateway/src/discord/gateway.py`, `sms-gateway/src/session/manager.py`, `sms-gateway/src/agent/client.py`

---

## 2. First-time Google OAuth consent

Any Google Workspace tool call on a fresh session → agent calls `get_auth_credential()` → IAM connector returns None (no stored token) → agent emits `adk_request_credential` event → gateway stores nonce in `pending_credentials/{nonce}` in Firestore → sends a Discord button pointing to `/oauth/authorize?nonce=...` → user clicks → gateway redirects to full Google OAuth URL → user consents → IAM connector callback hits `/oauth/connector/callback` → gateway calls `send_credential_response()` to resume the agent session → agent retries the tool call with a valid token → gateway also registers the Gmail push watch at this point.

**Key files:** `sms-gateway/src/discord/gateway.py` (`_send_auth_link`), `sms-gateway/src/oauth/handler.py`, `sms-gateway/src/session/manager.py` (`set_pending_credential`), `sms-gateway/src/agent/client.py` (`extract_credential_requests`, `send_credential_response`), `agents/schoopet/gcp_auth.py`

---

## 3. Email notification → autonomous agent action

Gmail receives email → Pub/Sub pushes to `/webhook/email` → OIDC token verified → look up `email_state/{address}` in Firestore to get `user_id` + last `history_id` → Gmail History API fetches new messages (filtered to Primary inbox, `CATEGORY_PRIMARY` label) → for each email, agent session created under `channel=email` → `_NOTIFICATION_WRAPPER` prompt with email metadata + user's email rules sent to agent → agent acts (create calendar event, create task, etc.) → response forwarded to Discord.

Special cases:
- If agent needs confirmation → declined, user told to come to Discord
- If agent needs credentials → pending_credential stored, auth link sent to Discord
- If agent starts response with `<SUPPRESS RESPONSE>` → discarded silently

**Key files:** `sms-gateway/src/email/handler.py`, `sms-gateway/src/email/gmail_client.py`, `agents/schoopet/email_tool.py`

---

## 4. Async task execution

Agent schedules a deferred task via `create_async_task` tool → task written to Firestore `async_tasks` + enqueued in Cloud Tasks → Cloud Tasks calls `/internal/tasks/execute` after the configured delay → `GatewayTaskExecutor` atomically claims the task (prevents double-execution) → creates an isolated ephemeral Agent Engine session → runs the task with pre-approved resource state (skips confirmation prompts for resources the user approved at scheduling time) → posts result to the original Discord channel. On failure, stores error and notifies Discord.

**Key files:** `sms-gateway/src/internal/task_executor.py`, `sms-gateway/src/internal/handler.py`, `agents/schoopet/tools/async_task_tool.py`

---

## 5. Confirmation flow (mutating Workspace operations)

Agent wants to create/modify a Google resource → `require_confirmation` gate fires → agent returns a pending confirmation event instead of executing → gateway stores it in `pending_approvals` in Firestore → sends Discord message with ✅/❌ buttons → user clicks → gateway sends `send_confirmation_response()` to Agent Engine → agent retries the tool call with `confirmed=True` and executes.

**Key files:** `agents/schoopet/resource_confirmation.py`, `sms-gateway/src/discord/gateway.py` (`_store_and_send_confirmations`), `sms-gateway/src/session/approvals.py`, `sms-gateway/src/agent/client.py` (`send_confirmation_response`)
