# Schoopet Architecture

This document describes the current implementation, not the historical target design.

## System Boundary

Schoopet is a personal assistant system with one active ADK agent deployed to Vertex AI Agent Engine per environment. The gateway is the only public dynamic service. It receives active channel events, manages sessions and OAuth, and calls the deployed agent.

```text
External channels
  Discord interactions, DMs, mentions
  Gmail Pub/Sub
        |
        v
Cloud Run: sms-gateway
  FastAPI routers
  signature validation
  session and approval state
  custom Google OAuth
  async task execution
        |
        v
Vertex AI Agent Engine: personal agent
  ADK LlmAgent
  memory tools
  Google Workspace tools
  async task tools
  subagents
        |
        v
Google APIs and cloud data
  Calendar, Drive, Docs, Sheets, Gmail
  Vertex AI Memory Bank
  BigQuery MCP
  Cloud Tasks
  Firestore
  Secret Manager
  GCS artifacts
```

## Agent

The agent lives in `agents/schoopet/`.

`root_agent.py` builds a single ADK `LlmAgent` named `personal`, wrapped for deployment with `AdkApp`. The default model is `gemini-3-flash-preview` through `GlobalGemini`.

The root agent has direct tools for:

- memory saves and ADK memory loading/preloading
- Calendar
- Drive
- Google Docs
- Google Sheets
- Gmail reads, fetches, artifacts, and rules
- user preferences and timezone handling
- date/time parsing and conversion
- async task creation, status, cancellation, and debugging

The root agent also uses subagents:

| Subagent | Purpose |
|---|---|
| `search_agent` | Current information and Google Search |
| `code_executor_agent` | Python-backed calculations and transformations through `AgentTool` |
| `structured_notes_agent` | BigQuery-backed structured data management |
| `deep_research_agent` | Multi-step research workflows, including background research tasks |

`deploy.py` updates an existing Vertex AI reasoning engine. Terraform creates the engine shell with Agent Identity; the deploy script updates the agent object, requirements, env vars, and memory bank config.

## Gateway

The gateway lives in `sms-gateway/` and runs on Cloud Run.

Routers are registered for Discord, email, OAuth, and internal endpoints.

`src/main.py` wires shared services during FastAPI lifespan startup:

- Firestore async client
- `AgentEngineClient`
- `SessionManager`
- `RateLimiter`
- Discord sender and validator
- OAuth manager
- email services
- internal task executor

Routers are registered for Discord, email, OAuth, and internal endpoints.

The gateway is also the active background-task worker.

## Sessions

Session state is stored in Firestore collection `sms_sessions`.

The session manager maps a channel user ID plus optional `session_scope` to a Vertex AI Agent Engine session ID. The base Firestore document ID is a normalized user ID. Scoped sessions append a SHA-256 hash prefix of the scope:

```text
<normalized-user-id>
<normalized-user-id>__<scope-hash>
```

Agent Engine session state includes:

- `channel`
- optional `session_scope`
- channel-specific metadata such as Discord channel IDs
- resource confirmation state such as `_resource_confirmed_<resource-id>`

Sessions expire after `SESSION_TIMEOUT_MINUTES` and are retired by deleting the Agent Engine session. Memory persistence is handled by the agent callback, not by the gateway.

## OAuth

The active OAuth system is Google Cloud Agent Identity IAM connectors (migrated from custom OAuth 2026-05-13, see [iam-connectors-adoption-plan.md](iam-connectors-adoption-plan.md)).

Agent tools call `get_workspace_service()` in `agents/schoopet/gcp_auth.py`. `GcpAuthProvider` is registered at module import. On each tool call, `CredentialManager.get_auth_credential()` queries the IAM connector for a stored token.

First-use flow (consent required):

1. IAM connector returns `uri_consent_required` with `auth_uri` and `nonce` → tool returns `None`.
2. ADK emits `adk_request_credential` event; gateway extracts it via `extract_credential_requests()`.
3. Gateway stores pending credential in Firestore `pending_credentials/{user_id}`.
4. Gateway sends a Discord button pointing to `/oauth/authorize?nonce=...&uid=...` (short URL — `auth_uri` exceeds Discord's URL length limit).
5. User clicks → gateway redirects to Google OAuth consent page.
6. User consents → IAM connector redirects to `/oauth/connector/callback?uid=...&user_id_validation_state=...`.
7. Gateway calls `credentials:finalize` at `iamconnectorcredentials.googleapis.com`.
8. After a 5-second propagation delay, gateway calls `send_credential_response()` to resume the agent.
9. Agent retries the tool; IAM connector returns stored token; tool executes.

Subsequent use: IAM connector returns stored token immediately; steps 2–8 are skipped.

Scopes: Calendar events, Drive, Docs, Sheets, Gmail read+modify, userinfo.email.

Legacy OAuth routes (`/oauth/google/*`, `OAuthManager`) are retained only for Gmail background push watch setup.

For the full flow with sequence diagrams and design notes, see [auth-flow.md](auth-flow.md).

## Confirmations

Mutating tools use ADK `require_confirmation`.

There are two broad confirmation shapes:

- one-off confirmations, such as creating or updating calendar events
- resource-scoped confirmations for existing Sheet, Doc, and Drive folder IDs

Resource-scoped confirmations store `_resource_confirmed_<resource-id>` in ADK session state after approval. Background tasks cannot ask live confirmation questions, so task creation can pass `allowed_resource_ids`. The gateway seeds the task Agent Engine session with the same state keys.

## Background Tasks

The root agent creates background tasks through `AsyncTaskTool`.

Immediate flow:

1. Agent calls `create_async_task`.
2. Tool writes an `async_tasks` Firestore document.
3. Tool creates a Cloud Task targeting `/internal/tasks/execute`.
4. Gateway authenticates the internal request.
5. Gateway atomically claims the task by moving `pending` or `scheduled` to `running`.
6. Gateway creates a fresh Agent Engine session with channel and allowed-resource state.
7. Gateway sends the task prompt to the agent.
8. Gateway records `completed` or `failed`.
9. Gateway sends the result to the configured Discord channel.

Scheduled flow:

- Cloud Tasks handles scheduled execution within its 720-hour limit.
- Tasks outside that practical window may remain in Firestore as `scheduled` without `cloud_task_name`.
- Cloud Scheduler calls `/internal/tasks/requeue-scheduled` weekly.
- The gateway creates Cloud Tasks for scheduled items now within the window.

Current limitation: `GatewayTaskExecutor` only supports Discord task notifications.

## Email Automation

The gateway receives Gmail Pub/Sub push notifications at `/webhook/email`. Email services use the same OAuth manager as user-initiated Google tools.

The agent prompt defines `INCOMING_EMAIL_NOTIFICATION` behavior:

- fetch the full email
- inspect user-defined email rules
- apply smart defaults for common actionable email classes
- notify only when action was taken or user input is needed

Gmail watches are renewed by Cloud Scheduler through `/internal/email/renew-watch`.

## Infrastructure

Terraform owns:

- Vertex AI reasoning engine shell
- Cloud Run gateway
- Artifact Registry
- Cloud Tasks queue
- Cloud Scheduler jobs
- Pub/Sub email topic
- Firestore indexes/rules configuration
- Secret Manager secrets
- GCS artifact bucket
- IAM and service accounts

Agent deployment owns the actual ADK agent package and Agent Engine config.

Gateway deployment builds a container and feeds the image digest back into Terraform.

## Web

The static frontend is in `web/`, not `website/`. It uses Vite with vanilla HTML, CSS, and JavaScript, and deploys through Firebase Hosting.
