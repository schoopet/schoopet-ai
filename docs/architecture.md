# Schoopet Architecture

This document describes the current implementation, not the historical target design.

## System Boundary

Schoopet is a personal assistant system with one active ADK agent deployed to Vertex AI Agent Engine per environment. The gateway is the only public dynamic service. It receives active channel events, manages sessions and OAuth, and calls the deployed agent.

```text
External channels
  Discord interactions, DMs, mentions
  Telegram
  Slack
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

Routers are registered for Discord, Telegram, Slack, email, OAuth, and internal endpoints.

`src/main.py` wires shared services during FastAPI lifespan startup:

- Firestore async client
- `AgentEngineClient`
- `SessionManager`
- `RateLimiter`
- channel senders and validators
- OAuth manager
- email services
- internal task executor

Routers are registered for Discord, Telegram, Slack, email, OAuth, and internal endpoints.

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

The active OAuth system is custom.

Flow:

1. Agent tool detects missing Google credentials and returns an authorization link.
2. User opens `/oauth/google/initiate`.
3. Gateway creates an `oauth_states` Firestore document with a TTL and feature.
4. Google redirects to `/oauth/google/callback`.
5. Gateway exchanges the code for tokens.
6. Access token metadata is stored in Firestore.
7. Refresh token is stored in Secret Manager.
8. Agent tools retrieve and refresh tokens through `agents/schoopet/oauth_client.py`.

The active configured OAuth feature is `google`, which covers Calendar, Drive, Docs, Sheets, and Gmail for personal users.

The IAM Connectors plan in [iam-connectors-adoption-plan.md](/Users/mmontan/schoopet/docs/iam-connectors-adoption-plan.md) is a future migration plan, not the active runtime.

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
