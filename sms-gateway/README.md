# Schoopet Gateway

The gateway is a FastAPI Cloud Run service that connects external channels to the single Schoopet personal Agent Engine. Despite the directory name, the active app is not SMS-specific.

## Responsibilities

- Receive Discord webhooks and Gmail Pub/Sub push notifications.
- Validate platform signatures where supported.
- Create and reuse Agent Engine sessions, with optional scoped sessions for channel/thread contexts.
- Handle native ADK confirmation requests and persist pending approvals in Firestore.
- Run the custom Google OAuth flow and store user tokens.
- Execute background tasks from Cloud Tasks at `/internal/tasks/execute`.
- Deliver task results back to Discord channels or active sessions.

## Runtime Shape

```text
Discord / Gmail
                 |
                 v
          FastAPI gateway
                 |
     Firestore sessions, OAuth state,
     tokens, approvals, async tasks
                 |
                 v
       Vertex AI Agent Engine
                 |
       Calendar / Drive / Docs /
       Sheets / Gmail / Search / Memory
```

## Main Modules

| Module | Purpose |
|---|---|
| `src/main.py` | App startup, dependency wiring, router registration |
| `src/config.py` | Environment-backed settings |
| `src/agent/client.py` | Vertex AI Agent Engine session and streaming client |
| `src/session/manager.py` | Firestore-backed session lifecycle and opt-in state |
| `src/session/approvals.py` | Pending ADK confirmation coordination |
| `src/oauth/` | Custom Google OAuth flow, token storage, HMAC token helper |
| `src/internal/handler.py` | Authenticated internal endpoints |
| `src/internal/task_executor.py` | Gateway-owned async task execution |
| `src/discord/` | Discord interactions, sender, Gateway WebSocket, validation |
| `src/email/` | Gmail Pub/Sub handling and watch setup |
| `src/ratelimit/` | Firestore-backed daily rate limiting |

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Service info |
| `/health` | GET | Cloud Run health check |
| `/webhook/discord` | POST | Discord interactions endpoint |
| `/webhook/email` | POST | Gmail Pub/Sub push endpoint |
| `/oauth/google/initiate` | GET | Start Google OAuth |
| `/oauth/google/callback` | GET | Google OAuth callback |
| `/internal/tasks/execute` | POST | Cloud Tasks execution target |
| `/internal/tasks/requeue-scheduled` | POST | Scheduler target for long-delay tasks |
| `/internal/user-notify` | POST | Authenticated direct notification endpoint |
| `/internal/email/renew-watch` | GET | Gmail watch renewal |

Internal endpoints require `verify_internal_request`, which accepts configured OIDC callers and HMAC where supported.

## Configuration

Required:

```bash
GOOGLE_CLOUD_PROJECT=...
GOOGLE_CLOUD_LOCATION=us-central1
PERSONAL_AGENT_ENGINE_ID=...
```

Common settings:

| Variable | Default | Meaning |
|---|---:|---|
| `SESSION_TIMEOUT_MINUTES` | `30` | Inactivity window before a new Agent Engine session is created |
| `AGENT_TIMEOUT_SECONDS` | `900` | Agent streaming timeout |
| `ENABLE_SIGNATURE_VALIDATION` | `true` | Platform signature validation switch |
| `DAILY_MESSAGE_LIMIT` | `1000` | Per-user daily rate limit |
| `ARTIFACT_BUCKET_NAME` | `<project>-agent-artifacts` | Artifact bucket |
| `EMAIL_PUBSUB_TOPIC` | empty | Gmail watch Pub/Sub topic |

Optional Discord secrets/config:

- `DISCORD_BOT_TOKEN`, `DISCORD_PUBLIC_KEY`, `DISCORD_APPLICATION_ID`

OAuth:

- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `GOOGLE_OAUTH_REDIRECT_URI`
- `OAUTH_STATE_TTL_SECONDS`

## Storage

Firestore collections:

| Collection | Purpose |
|---|---|
| `sms_sessions` | Session documents, opt-in state, channel metadata, pending confirmations |
| `oauth_states` | Short-lived OAuth CSRF state |
| `oauth_tokens` | User access tokens and metadata; refresh tokens are in Secret Manager |
| `async_tasks` | Background task definitions, status, results, delivery metadata |

Session documents use a normalized user ID as the base document ID. Scoped sessions append a stable hash of `session_scope` so one user can have multiple concurrent scoped conversations.

## Async Tasks

The agent creates tasks with `AsyncTaskTool`. That stores an `async_tasks` document and creates a Cloud Task targeting:

```text
POST <SMS_GATEWAY_URL>/internal/tasks/execute
```

The gateway claims the Firestore task with an update-time precondition before executing it. Execution happens in a fresh Agent Engine session. After execution, the gateway deletes that task session and delivers the result.

Only Discord task notifications are currently supported by `GatewayTaskExecutor`.

Scheduled tasks more than 720 hours out can remain in Firestore without `cloud_task_name`. Cloud Scheduler calls `/internal/tasks/requeue-scheduled` weekly to create Cloud Tasks when they enter the 30-day window.

## Local Development

```bash
cd sms-gateway
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

set -a && source ../environments/dev.env && set +a
uvicorn src.main:app --reload --port 8080
```

Run tests:

```bash
python -m pytest tests/ -v --tb=short
```

From the repo root:

```bash
make test-sms-gateway
```

## Deployment

From the repo root:

```bash
./sms-gateway/scripts/deploy.sh --env=prod
```

The script builds and pushes the container, resolves the image digest, then applies Terraform. Cloud Run service configuration is managed in `terraform/sms_gateway.tf`.
